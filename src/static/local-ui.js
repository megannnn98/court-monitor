// Three small conveniences for the operator; the pages work without any of them.

// 1. A button that reloads the page (a form post and the redirect back) must not throw
//    the reader to the top of a long list: the scroll position is kept across that one
//    reload, for the same page only.
(function () {
  const KEY = "scroll:" + location.pathname;
  try {
    const saved = sessionStorage.getItem(KEY);
    if (saved !== null) {
      sessionStorage.removeItem(KEY);
      window.scrollTo(0, Number(saved));
    }
    document.addEventListener("submit", function (event) {
      if (event.target.method === "post") {
        sessionStorage.setItem(KEY, String(window.scrollY));
      }
    });
  } catch (error) {
    // No storage (private mode): the page simply opens at the top, as before.
  }
})();

// 2. A link that leaves the console (a source, a publication) opens in a new tab, so the
//    list the operator was reading stays where it was.
document.querySelectorAll('a[href^="http"]').forEach(function (link) {
  if (link.host !== location.host) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
});

// 3. «Скопировать» beside a name: the name goes to the clipboard as it is shown.
document.addEventListener("click", function (event) {
  const button = event.target.closest("button[data-copy]");
  if (!button || !navigator.clipboard) {
    return;
  }
  navigator.clipboard.writeText(button.dataset.copy).then(function () {
    button.classList.add("copied");
    setTimeout(function () {
      button.classList.remove("copied");
    }, 1200);
  });
});
