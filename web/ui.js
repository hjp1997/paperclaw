"use strict";

window.PaperClawUI = (() => {
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function link(label, url) {
    try {
      const parsed = new URL(url);
      if (!["https:", "http:"].includes(parsed.protocol)) return el("span", "", label);
      const anchor = el("a", "", label);
      anchor.href = parsed.href;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      return anchor;
    } catch {
      return el("span", "", label);
    }
  }

  function paperTitle(paper) {
    const heading = el("h3");
    if (!paper.title_parts) {
      heading.textContent = paper.title_text || paper.title;
      return heading;
    }
    heading.setAttribute("aria-label", paper.title_text);
    for (const part of paper.title_parts) {
      if (!part.math) {
        heading.append(part.text);
        continue;
      }
      const fragment = el("span", "title-math", part.text);
      try {
        window.katex.render(part.math, fragment, {
          output:"mathml", trust:false, throwOnError:true, maxExpand:200, maxSize:6
        });
      } catch {
        fragment.textContent = part.text;
      }
      heading.append(fragment);
    }
    return heading;
  }

  return Object.freeze({el, link, paperTitle});
})();
