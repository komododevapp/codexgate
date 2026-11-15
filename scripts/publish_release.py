"""Publish only verified native artifacts from a separate private CI run."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

REPOSITORY = 'komododevapp/codexgate'
MATRIX = {('linux', 'x86_64'), ('linux', 'aarch64'), ('macos', 'x86_64'), ('macos', 'aarch64')}
MAX_ARTIFACT = 512 * 1024 * 1024
MAX_INSTALLER = 256 * 1024
MAX_SUMS = 64 * 1024


class PublishError(ValueError):
    """Controlled error text never includes download URLs or provider output."""


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def digest_value(value):
    if not isinstance(value, str):
        raise PublishError('Artifact SHA-256 is required')
    value = value.removeprefix('sha256:')
    if not re.fullmatch(r'[0-9a-fA-F]{64}', value):
        raise PublishError('Artifact SHA-256 is invalid')
    return value.lower()


def validate_url(url):
    if not isinstance(url, str):
        raise PublishError('Artifact download URL is required')
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        valid = (parsed.scheme == 'https' and parsed.port in {None, 443}
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and re.fullmatch(r'productionresults[a-z0-9-]*\.blob\.core\.windows\.net', host)
                 and re.fullmatch(r'/actions-results/[A-Za-z0-9._/-]+\.zip', parsed.path)
                 and '..' not in parsed.path.split('/') and '\\' not in url
                 and not any(ord(char) < 32 for char in url))
    except ValueError:
        valid = False
    if not valid:
        raise PublishError('Artifact URL is outside the approved GitHub Actions HTTPS result host')
    return url


def parse_inputs(values):
    version = values.get('version', '')
    build = values.get('build', '')
    run = values.get('source_run_id', '')
    tag = values.get('release_tag', '')
    if (not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version)
            or not isinstance(build, str) or not re.fullmatch(r'[1-9]\d*', build)
            or not isinstance(run, str) or not re.fullmatch(r'[1-9]\d*', run)
            or tag != f'v{version}-build{build}'):
        raise PublishError('Release version/build/tag/source run inputs are invalid')
    try:
        artifacts = json.loads(values.get('artifacts_json', ''))
    except (TypeError, ValueError):
        raise PublishError('Artifact metadata must be a JSON list') from None
    if not isinstance(artifacts, list) or len(artifacts) != 4:
        raise PublishError('Exactly four native artifact entries are required')
    seen, ids = set(), set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {'platform', 'arch', 'artifact_id', 'url', 'digest'}:
            raise PublishError('Artifact entry fields are invalid')
        if not isinstance(item['platform'], str) or not isinstance(item['arch'], str):
            raise PublishError('Native artifact platform/architecture must be strings')
        target = (item['platform'], item['arch'])
        if target not in MATRIX or target in seen:
            raise PublishError('Native artifact platform/architecture is missing or duplicated')
        identifier = item['artifact_id']
        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0 or identifier in ids:
            raise PublishError('Artifact IDs must be distinct positive integers')
        seen.add(target)
        ids.add(identifier)
        item['url'] = validate_url(item['url'])
        item['digest'] = digest_value(item['digest'])
    notes = values.get('release_notes', '')
    if not isinstance(notes, str) or len(notes.encode('utf-8')) > MAX_SUMS:
        raise PublishError('Release notes exceed the supported UTF-8 size')
    return {'version': version, 'build': build, 'tag': tag, 'run': run, 'artifacts': artifacts, 'notes': notes}


class SafeRedirect(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(item, path):
    deadline = time.monotonic() + 300
    opener = build_opener(SafeRedirect())
    try:
        with opener.open(validate_url(item['url']), timeout=30) as source, path.open('wb') as target:
            validate_url(source.geturl())
            length = source.headers.get('Content-Length')
            if length and (not length.isdigit() or int(length) > MAX_ARTIFACT):
                raise PublishError('GitHub artifact exceeds the download bound')
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARTIFACT or time.monotonic() > deadline:
                    raise PublishError('GitHub artifact exceeds its size or time bound')
                target.write(chunk)
            if length and total != int(length):
                raise PublishError('GitHub artifact download is incomplete')
    except (OSError, URLError):
        raise PublishError('Artifact download failed; refresh its signed URL before retrying') from None


def checksums(raw, expected):
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        raise PublishError('Checksum manifest is not UTF-8') from None
    entries = {}
    for line in lines:
        if not line.strip():
            continue
        match = re.fullmatch(r'([0-9a-fA-F]{64})[ \t]+\*?([A-Za-z0-9._+-]+)', line)
        if not match or match[2] in entries:
            raise PublishError('Checksum manifest has an invalid or duplicated entry')
        entries[match[2]] = match[1].lower()
    if set(entries) != expected:
        raise PublishError('Checksum manifest does not exactly match its artifact files')
    return entries


def prepare(inputs, root, downloader=download):
    root = Path(root)
    assets = root / 'assets'
    assets.mkdir()
    merged, installer_digest = {}, None
    for item in inputs['artifacts']:
        name = f"codexgate-{inputs['version']}-build{inputs['build']}-{item['platform']}-{item['arch']}.tar.gz"
        archive = root / f"artifact-{item['artifact_id']}.zip"
        downloader(item, archive)
        if archive.stat().st_size > MAX_ARTIFACT or sha256(archive) != item['digest']:
            raise PublishError('Artifact ZIP SHA-256 does not match GitHub metadata')
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                expected = {name, 'install.sh', 'SHA256SUMS'}
                if len(members) != 3 or {entry.filename for entry in members} != expected:
                    raise PublishError('Artifact ZIP must contain exactly the expected three root files')
                if sum(entry.file_size for entry in members) > MAX_ARTIFACT:
                    raise PublishError('Expanded artifact ZIP exceeds the size bound')
                for entry in members:
                    mode = entry.external_attr >> 16
                    if entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                        raise PublishError('Artifact ZIP contains a non-regular file')
                    maximum = MAX_INSTALLER if entry.filename == 'install.sh' else MAX_SUMS if entry.filename == 'SHA256SUMS' else MAX_ARTIFACT
                    if entry.file_size > maximum:
                        raise PublishError('Artifact ZIP member exceeds its size bound')
                manifest = checksums(bundle.read('SHA256SUMS'), {name, 'install.sh'})
                for filename in (name, 'install.sh'):
                    destination = root / ('installer-' + str(item['artifact_id'])) if filename == 'install.sh' else assets / filename
                    total = 0
                    with bundle.open(filename) as source, destination.open('wb') as target:
                        while chunk := source.read(1024 * 1024):
                            total += len(chunk)
                            if total > (MAX_INSTALLER if filename == 'install.sh' else MAX_ARTIFACT):
                                raise PublishError('Expanded artifact file exceeds its size bound')
                            target.write(chunk)
                    digest = sha256(destination)
                    if digest != manifest[filename]:
                        raise PublishError('Artifact file does not match its release checksum')
                    if filename in merged and merged[filename] != digest:
                        raise PublishError('Native artifacts contain conflicting checksums')
                    merged[filename] = digest
                    if filename == 'install.sh':
                        if installer_digest is not None and installer_digest != digest:
                            raise PublishError('Native artifacts contain different installers')
                        installer_digest = digest
                        if not (assets / 'install.sh').exists():
                            destination.rename(assets / 'install.sh')
                        else:
                            destination.unlink()
        except (zipfile.BadZipFile, RuntimeError, EOFError):
            raise PublishError('GitHub artifact ZIP is invalid or corrupt') from None
    (assets / 'SHA256SUMS').write_text(''.join(f'{digest}  {name}\n' for name, digest in sorted(merged.items())), encoding='utf-8')
    files = {path.name: {'path': path, 'size': path.stat().st_size, 'digest': sha256(path)} for path in assets.iterdir()}
    if len(files) != 6:
        raise PublishError('Publisher did not assemble exactly six release assets')
    return files


class GitHub:
    def __init__(self, repo=REPOSITORY):
        if repo != REPOSITORY:
            raise PublishError('Publisher repository is not the approved public repository')
        self.repo = repo

    def command(self, arguments, *, allow_404=False):
        try:
            result = subprocess.run(['gh', *arguments], capture_output=True, text=True, timeout=1200, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise PublishError('GitHub command failed or timed out; reconcile release state before retrying') from None
        if result.returncode:
            if allow_404 and re.search(r'HTTP 404', result.stderr):
                return None
            raise PublishError('GitHub command failed; rerun the workflow to reconcile state, do not overwrite assets')
        return result.stdout

    def release(self, tag):
        raw = self.command(['api', f'repos/{self.repo}/releases/tags/{quote(tag, safe="")}'], allow_404=True)
        if raw is None:
            return None
        try:
            result = json.loads(raw)
        except ValueError:
            raise PublishError('GitHub release response is invalid') from None
        if not isinstance(result, dict):
            raise PublishError('GitHub release response is invalid')
        return result

    def create(self, inputs, notes):
        self.command(['release', 'create', inputs['tag'], '--repo', self.repo, '--draft', '--target', 'main',
                      '--title', f"CodexGate {inputs['version']} · build {inputs['build']}", '--notes-file', str(notes)])

    def upload(self, tag, file):
        self.command(['release', 'upload', tag, str(file['path']), '--repo', self.repo])

    def publish(self, tag):
        self.command(['release', 'edit', tag, '--repo', self.repo, '--draft=false', '--latest'])


def exact_asset(asset, file):
    try:
        return (asset.get('state') == 'uploaded' and asset.get('size') == file['size']
                and digest_value(asset.get('digest')) == file['digest'])
    except PublishError:
        return False


def inventory(release, files, inputs):
    if (release.get('tag_name') != inputs['tag'] or release.get('target_commitish') != 'main'
            or release.get('prerelease') is not False or not isinstance(release.get('draft'), bool)):
        raise PublishError('Existing release identity/target does not match the intended public release')
    rows = release.get('assets')
    if not isinstance(rows, list):
        raise PublishError('GitHub release asset list is invalid')
    seen = {}
    for asset in rows:
        if not isinstance(asset, dict) or asset.get('name') not in files or asset['name'] in seen:
            raise PublishError('Existing release has unexpected or duplicate assets; nothing overwritten')
        if not exact_asset(asset, files[asset['name']]):
            raise PublishError('Existing asset size/digest differs or is unknown; nothing overwritten')
        seen[asset['name']] = asset
    return seen


def publish(inputs, files, root, github):
    notes = Path(root) / 'release-notes.md'
    notes.write_text(inputs['notes'], encoding='utf-8')
    release = github.release(inputs['tag'])
    if release is None:
        github.create(inputs, notes)
        release = github.release(inputs['tag'])
        if release is None:
            raise PublishError('Draft creation outcome is unknown; inspect it before retrying')
    present = inventory(release, files, inputs)
    if not release['draft']:
        if set(present) != set(files):
            raise PublishError('Published release is incomplete; refusing to modify it')
        return 'already_published'
    for name, file in sorted(files.items()):
        if name not in present:
            github.upload(inputs['tag'], file)
            # A new read verifies each upload rather than trusting command success.
            release = github.release(inputs['tag'])
            if release is None or name not in inventory(release, files, inputs):
                raise PublishError('Asset upload is not confirmed; inspect release state before retrying')
    release = github.release(inputs['tag'])
    if release is None or set(inventory(release, files, inputs)) != set(files):
        raise PublishError('All six assets must be verified before publication')
    github.publish(inputs['tag'])
    final = github.release(inputs['tag'])
    if final is None or final.get('draft') is not False or set(inventory(final, files, inputs)) != set(files):
        raise PublishError('Publication outcome is not confirmed; inspect release state before retrying')
    return 'published'


def main():
    try:
        event = Path(os.environ['GITHUB_EVENT_PATH'])
        values = json.loads(event.read_text(encoding='utf-8')).get('inputs', {})
        # Read signed links from the event file, not printed run-step env values.
        # Register masks before any subsequent operation can log metadata.
        try:
            raw = json.loads(values.get('artifacts_json', ''))
            for item in raw if isinstance(raw, list) else []:
                if isinstance(item, dict) and isinstance(item.get('url'), str):
                    value = item['url'].replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                    print('::add-mask::' + value, flush=True)
        except (TypeError, ValueError):
            pass
        values['release_notes'] = os.environ.get('RELEASE_NOTES', values.get('release_notes', ''))
        inputs = parse_inputs(values)
        github = GitHub(os.environ.get('GITHUB_REPOSITORY', ''))
        with tempfile.TemporaryDirectory(prefix='codexgate-publisher-') as temporary:
            files = prepare(inputs, temporary)
            state = publish(inputs, files, temporary, github)
        url = f"https://github.com/{REPOSITORY}/releases/tag/{inputs['tag']}"
        print(f'CodexGate release {state}: {url}')
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a', encoding='utf-8') as summary:
                summary.write(f'CodexGate {inputs["version"]} build {inputs["build"]}: [{state}]({url})\n')
    except PublishError as exc:
        # These messages are controlled constants, never URL/provider payloads.
        print('::error::' + str(exc), file=sys.stderr)
        return 1
    except (OSError, KeyError, TypeError, ValueError):
        print('::error::Publication stopped before confirmation; inspect release state and refresh artifact metadata before retrying.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
