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
