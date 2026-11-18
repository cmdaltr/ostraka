import { useEffect } from 'react';

// Every card with a titled header folds away when its title is clicked, and stays folded on the next visit
// (per browser, keyed by the kind of page and the title, so folding "Summary" folds it on every incident).
// Folded content is hidden "until found": the browser's find-in-page still searches it and opens the card
// on a match. Where a browser can't do that, cards don't fold at all, so a search never misses anything.

const KEY = 'ostraka.folded';
const CARD = '.panel';
const TITLE = ':scope > header h2, :scope > .panel-head h2, :scope > .links-head h2';

export const canFold = typeof document !== 'undefined' && 'onbeforematch' in document.body;

function load(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY) ?? '[]') as string[]);
  } catch {
    return new Set();
  }
}

function store(folded: Set<string>) {
  try {
    localStorage.setItem(KEY, JSON.stringify([...folded]));
  } catch {
    // a private window: it just won't be remembered
  }
}

// The kind of page, not the record: references and ids drop out
export function pageKind(path: string, search: string): string {
  const kind = path
    .split('/')
    .map((part) => (/^([A-Z]{3}-\d{2}-\d+|[0-9a-f-]{36})$/i.test(part) ? '*' : part))
    .join('/');
  const section = new URLSearchParams(search).get('s');
  return section ? `${kind}?s=${section}` : kind;
}

function titleOf(card: Element): HTMLElement | null {
  return card.querySelector<HTMLElement>(TITLE);
}

function head(card: Element, title: HTMLElement): Element {
  let el: Element = title;
  while (el.parentElement && el.parentElement !== card) el = el.parentElement;
  return el;
}

function keyOf(title: HTMLElement) {
  return `${pageKind(location.pathname, location.search)}|${title.textContent?.trim() ?? ''}`;
}

function show(card: Element, folded: boolean) {
  const title = titleOf(card);
  if (!title) return;
  const top = head(card, title);
  card.classList.toggle('is-folded', folded);
  title.title = folded ? 'Show' : 'Fold away';
  for (const child of card.children) {
    if (child === top) continue;
    if (folded) child.setAttribute('hidden', 'until-found');
    else if (child.getAttribute('hidden') === 'until-found') child.removeAttribute('hidden');
  }
}

function prepare(card: Element, folded: Set<string>) {
  const title = titleOf(card);
  if (!title) return;
  if (!title.dataset.folds) {
    title.dataset.folds = 'yes';
    // it stays a heading, so it's still found by heading; it takes focus so a key can fold it
    title.tabIndex = 0;
  }
  show(card, folded.has(keyOf(title)));
}

function toggle(card: Element, open: boolean) {
  const title = titleOf(card);
  if (!title) return;
  const folded = load();
  const key = keyOf(title);
  if (open) folded.delete(key);
  else folded.add(key);
  store(folded);
  show(card, !open);
}

export function useFoldingCards(root: { current: HTMLElement | null }, path: string) {
  useEffect(() => {
    const el = root.current;
    if (!el || !canFold) return;
    const all = () => {
      const folded = load();
      el.querySelectorAll(CARD).forEach((card) => prepare(card, folded));
    };
    all();
    // sections load on demand and cards appear as they do; a folded card's new children fold with it
    const watch = new MutationObserver((changes) => {
      const folded = load();
      const cards = new Set<Element>();
      for (const c of changes) {
        const target = c.target as Element;
        if (target.matches?.(CARD)) cards.add(target);
        c.addedNodes.forEach((n) => {
          if (n instanceof Element) {
            if (n.matches(CARD)) cards.add(n);
            n.querySelectorAll(CARD).forEach((card) => cards.add(card));
            const parent = n.parentElement?.closest(CARD);
            if (parent) cards.add(parent);
          }
        });
      }
      cards.forEach((card) => prepare(card, folded));
    });
    watch.observe(el, { childList: true, subtree: true });

    const click = (e: Event) => {
      const title = (e.target as Element).closest<HTMLElement>('[data-folds]');
      if (!title) return;
      if (e instanceof KeyboardEvent) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
      }
      const card = title.closest(CARD);
      if (card) toggle(card, card.classList.contains('is-folded'));
    };
    // find-in-page opened a folded part: open the whole card, and remember it open
    const found = (e: Event) => {
      const card = (e.target as Element).closest(CARD);
      if (card?.classList.contains('is-folded')) toggle(card, true);
    };
    el.addEventListener('click', click);
    el.addEventListener('keydown', click);
    el.addEventListener('beforematch', found, true);
    return () => {
      watch.disconnect();
      el.removeEventListener('click', click);
      el.removeEventListener('keydown', click);
      el.removeEventListener('beforematch', found, true);
    };
  }, [root, path]);
}
