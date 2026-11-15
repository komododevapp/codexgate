/* Progressive enhancements only: every command and instruction remains in HTML. */
(() => {
  document.documentElement.classList.add('enhanced');
  const filter = document.querySelector('#command-filter');
  const commands = [...document.querySelectorAll('[data-command]')];
  const count = document.querySelector('#command-count');
  const empty = document.querySelector('#empty-results');
  if (filter && count && empty) {
    const update = () => {
      const query = filter.value.trim().toLowerCase();
      let visible = 0;
      commands.forEach((command) => {
        const matches = command.textContent.toLowerCase().includes(query);
        command.hidden = !matches;
        if (matches) visible += 1;
      });
      document.querySelectorAll('.command-group').forEach((group) => {
        group.hidden = !group.querySelector('[data-command]:not([hidden])');
      });
      count.textContent = `${visible} ${visible === 1 ? 'entry' : 'entries'}`;
      empty.hidden = visible !== 0;
    };
    filter.addEventListener('input', update);
    update();
  }

  const status = document.querySelector('#copy-status');
  let dismiss;
  const announce = (message) => {
    if (!status) return;
    clearTimeout(dismiss);
    status.textContent = message;
    status.classList.add('visible');
    dismiss = setTimeout(() => status.classList.remove('visible'), 3400);
  };
  document.querySelectorAll('.copy-button').forEach((button) => {
    button.addEventListener('click', async () => {
      const code = button.parentElement.querySelector('code');
      if (!code) return;
      try {
        if (!navigator.clipboard || !window.isSecureContext) throw new Error('unavailable');
        await navigator.clipboard.writeText(code.textContent);
        announce('Commands copied. Replace any placeholders before running.');
      } catch {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(code);
        selection.removeAllRanges();
        selection.addRange(range);
        announce('Commands selected. Use your browser’s Copy action.');
      }
    });
  });


  // Release metadata supplies links only. No remote HTML or code is executed.
  const target = document.querySelector('#release-target');
  const releaseStatus = document.querySelector('#release-status');
  const installPanel = document.querySelector('#binary-install');
  const updatePanel = document.querySelector('#binary-update');
  const unavailable = document.querySelector('#binary-update-unavailable');
  const installCode = document.querySelector('#binary-install-code');
  const updateCode = document.querySelector('#binary-update-code');
  const shellPanel = document.querySelector('#shell-install');
  const shellCode = document.querySelector('#shell-install-code');
  const releaseApi = 'https://api.github.com/repos/komododevapp/codexgate/releases/latest';
  const assetPrefix = 'https://github.com/komododevapp/codexgate/releases/download/';
  const safeAsset = (asset, name) => {
    if (!asset || asset.name !== name || typeof asset.browser_download_url !== 'string') return null;
    const url = asset.browser_download_url;
    // Restrict shell interpolation to official, simple GitHub download URLs.
    if (!url.startsWith(assetPrefix) || !/^[A-Za-z0-9._~:/%+-]+$/.test(url)) return null;
    if (url.slice(assetPrefix.length).split('/').length !== 2 || !url.endsWith(`/${name}`)) return null;
    return url;
  };
  const makeCommands = (asset, checksum, version, build, platform) => {
    const verify = platform === 'macos' ? 'shasum -a 256 -c selected.sha256' : 'sha256sum -c selected.sha256';
    return [
      'set -euo pipefail',
      'export PATH="$HOME/.local/bin:$PATH"',
      'app_base="$HOME/.local/share/codexgate"',
      `release_dir="$app_base/releases/${version}-build${build}"`,
      'mkdir -p "$app_base/releases" "$HOME/.local/bin"',
      '[ ! -e "$release_dir" ] || { echo "This version is already installed; do not overwrite it." >&2; exit 1; }',
      'download_dir="$(mktemp -d)"',
      'staged_dir="$(mktemp -d "$app_base/releases/.install.XXXXXX")"',
      'trap \'rm -rf -- "$download_dir" "$staged_dir"\' EXIT',
      `archive='${asset.name}'`,
      `curl --fail --location --proto '=https' --proto-redir '=https' '${asset.browser_download_url}' -o "$download_dir/$archive"`,
      `curl --fail --location --proto '=https' --proto-redir '=https' '${checksum.browser_download_url}' -o "$download_dir/SHA256SUMS"`,
      '(',
      '  cd "$download_dir"',
      '  awk -v archive="$archive" \'$2 == archive || $2 == "*" archive { print; found++ } END { if (found != 1) exit 1 }\' SHA256SUMS > selected.sha256',
      `  ${verify}`,
      ')',
      'tar -xzf "$download_dir/$archive" -C "$staged_dir" --strip-components=1',
      '[ -x "$staged_dir/codexgate" ] && [ -d "$staged_dir/_internal" ]',
      'mv "$staged_dir" "$release_dir"',
      'ln -sfn "$release_dir" "$app_base/current"',
      'ln -sfn "$app_base/current/codexgate" "$HOME/.local/bin/codexgate"',
      'codexgate --help',
    ].join('\n');
  };
  if (target && releaseStatus && installPanel && updatePanel && unavailable && installCode && updateCode) {
    let release;
    const hideCommands = (message) => {
      if (shellPanel) shellPanel.hidden = true;
      if (shellCode) shellCode.textContent = '';
      installPanel.hidden = true;
      updatePanel.hidden = true;
      unavailable.hidden = false;
      installCode.textContent = '';
      updateCode.textContent = '';
      releaseStatus.textContent = message;
    };
    const renderRelease = () => {
      if (!release || !Array.isArray(release.assets)) return;
      const [platform, arch] = target.value.split('-');
      const pattern = new RegExp(`^codexgate-(\\d+\\.\\d+\\.\\d+)-build(\\d+)-${platform}-${arch}\\.tar\\.gz$`);
      const archives = release.assets.filter((asset) => asset && typeof asset.name === 'string' && pattern.test(asset.name));
      const sums = release.assets.filter((asset) => asset && asset.name === 'SHA256SUMS');
      const archive = archives.length === 1 ? archives[0] : null;
      const checksum = sums.length === 1 ? sums[0] : null;
      if (!archive || !checksum || !safeAsset(archive, archive.name) || !safeAsset(checksum, 'SHA256SUMS')) {
        hideCommands('The latest public release does not yet include a complete download for this target. Check GitHub Releases or choose another target.');
        return;
      }
      const [, version, build] = archive.name.match(pattern);
      const commands = makeCommands(archive, checksum, version, build, platform);
      const installers = release.assets.filter((asset) => asset && asset.name === 'install.sh');
      const installer = installers.length === 1 && safeAsset(installers[0], 'install.sh');
      if (shellPanel && shellCode) {
        shellPanel.hidden = !installer;
        shellCode.textContent = installer ? [
          "curl --fail --location --proto '=https' --proto-redir '=https' https://github.com/komododevapp/codexgate/releases/latest/download/install.sh -o install-codexgate.sh &&",
          'sh install-codexgate.sh',
        ].join('\n') : '';
      }
      installCode.textContent = commands;
      updateCode.textContent = [
        'set -euo pipefail',
        'export PATH="$HOME/.local/bin:$PATH"',
        'cd "$HOME/codexgate-gateway"',
        'codexgate admin service stop',
        'codexgate service stop',
        commands,
        'codexgate migrate',
        'codexgate service install --project-dir "$HOME/codexgate-gateway"',
        'codexgate admin service install --project-dir "$HOME/codexgate-gateway"',
        'codexgate service start',
        'codexgate admin service start',
        'codexgate service status',
        'codexgate admin service status',
      ].join('\n');
      installPanel.hidden = false;
      updatePanel.hidden = false;
      unavailable.hidden = true;
      releaseStatus.textContent = `Available: CodexGate ${version} · build ${build}. Review the commands before running them.`;
    };
    target.addEventListener('change', renderRelease);
    hideCommands('Checking the latest public GitHub Release…');
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    fetch(releaseApi, { headers: { Accept: 'application/vnd.github+json' }, signal: controller.signal })
      .then(async (response) => {
        if (response.status === 404) {
          hideCommands('No public binary release is available yet. Installation commands will appear here after publication.');
          return;
        }
        if (!response.ok) throw new Error('release unavailable');
        release = await response.json();
        if (!release || release.draft || release.prerelease || !Array.isArray(release.assets)) throw new Error('incomplete release');
        renderRelease();
      })
      .catch(() => hideCommands('Release availability could not be checked. Open GitHub Releases to check manually; no download has been selected.'))
      .finally(() => clearTimeout(timeout));
  }

  if ('IntersectionObserver' in window) {
    const links = [...document.querySelectorAll('.docs-nav a[href^="#"]')];
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        links.forEach((link) => {
          if (link.hash === `#${entry.target.id}`) link.setAttribute('aria-current', 'location');
          else link.removeAttribute('aria-current');
        });
      }
    }, { rootMargin: '-15% 0px -70% 0px' });
    links.forEach((link) => {
      const target = document.getElementById(link.hash.slice(1));
      if (target) observer.observe(target);
    });
  }
})();
