// ============================================================
//  文枢 · 番茄榜单注入验证脚本（浏览器控制台版）
//
//  用途：在真实浏览器里确认三件事，决定「注入」方案是否值得做。
//
//    1. DOM 里的书名是**正常汉字**还是**私用区码位**（最关键）
//    2. 滚动加载后，新数据去了哪里（DOM / 全局变量）
//    3. 一次能采到多少本，字段有多全
//
//  【怎么用】
//    1. 用浏览器打开任意番茄榜单页，例如：
//       https://fanqienovel.com/rank/0_1_1139
//    2. 按 F12 打开开发者工具 → Console
//    3. 把本文件**全部内容**粘贴进去，回车
//    4. 等它跑完（会自己滚动几屏），把输出结果发我
//
//  【安全性】
//    - 只读：不修改页面、不点击、不登录
//    - 不外发：不发任何网络请求，结果只打印在控制台
//    - 可中断：任何时候刷新页面即停止
// ============================================================

(async function () {
  'use strict';

  const log = (...a) => console.log('%c[文枢验证]', 'color:#2563eb;font-weight:bold', ...a);
  const warn = (...a) => console.warn('%c[文枢验证]', 'color:#d97706;font-weight:bold', ...a);

  // ---------- 判定工具 ----------
  const isPUA = (ch) => {
    const c = ch.codePointAt(0);
    return c >= 0xE000 && c <= 0xF8FF;
  };

  const analyzeText = (s) => {
    const text = String(s || '');
    let pua = 0;
    const puaSamples = [];
    for (const ch of text) {
      if (isPUA(ch)) {
        pua++;
        if (puaSamples.length < 6) {
          puaSamples.push('U+' + ch.codePointAt(0).toString(16).toUpperCase());
        }
      }
    }
    return {
      text,
      length: text.length,
      puaCount: pua,
      puaRatio: text.length ? +(pua / text.length * 100).toFixed(1) : 0,
      puaSamples,
    };
  };

  const show = (s) => {
    // 把私用区字符显示成 □，便于肉眼看乱码比例
    return String(s || '').split('').map((c) => (isPUA(c) ? '□' : c)).join('');
  };

  console.log('%c══════════════════════════════════════════════', 'color:#2563eb');
  log('开始验证。页面：' + location.href);
  console.log('%c══════════════════════════════════════════════', 'color:#2563eb');

  // ============================================================
  //  第 1 步：页面首屏的 DOM 结构
  // ============================================================
  log('【1/4】检查页面结构');

  const listSel = [
    '.muye-rank-book-list',
    '.rank-book-item',
    '[class*="rank-book"]',
    '[class*="book-item"]',
  ];

  let listEl = null;
  let matchedSel = '';
  for (const sel of listSel) {
    const el = document.querySelector(sel);
    if (el) {
      listEl = el;
      matchedSel = sel;
      break;
    }
  }

  if (!listEl) {
    warn('没找到榜单列表容器。请确认当前是榜单页，并把下面这段发我：');
    console.log('页面上所有 class 含 rank 的元素：');
    document.querySelectorAll('[class*="rank"]').forEach((el, i) => {
      if (i < 20) console.log('  ', el.tagName, el.className);
    });
    return;
  }

  log('找到列表容器：' + matchedSel);
  log('容器内子元素数：' + listEl.children.length);

  // ============================================================
  //  第 2 步：取一本书，逐字段分析是否含私用区字符
  // ============================================================
  log('');
  log('【2/4】分析书名等字段的字符（最关键的一步）');

  // 试着从列表里定位「书名」元素
  const firstItem = listEl.querySelector('[class*="book-item"], li, .rank-book-item')
    || listEl.children[0];

  if (!firstItem) {
    warn('列表里没有条目');
    return;
  }

  console.log('第一个条目的 HTML（截断 800 字）：');
  console.log(firstItem.outerHTML.slice(0, 800));

  // 分析条目里所有文本节点
  const walker = document.createTreeWalker(firstItem, NodeFilter.SHOW_TEXT);
  const texts = [];
  let node;
  while ((node = walker.nextNode())) {
    const t = (node.nodeValue || '').trim();
    if (t) texts.push(t);
  }

  log('条目里的文本片段数：' + texts.length);
  console.log('');
  console.log('逐段分析（□ = 私用区字符）：');
  console.log('  序号 | 原字符数 | 私用区 | 占比  | 显示效果');
  console.log('  -----|---------|--------|-------|--------');
  texts.slice(0, 12).forEach((t, i) => {
    const a = analyzeText(t);
    console.log(
      `  ${String(i + 1).padStart(4)} | ${String(a.length).padStart(7)} | ` +
      `${String(a.puaCount).padStart(6)} | ${String(a.puaRatio).padStart(5)}% | ${show(a.text).slice(0, 40)}`
    );
  });

  // 汇总
  const allText = texts.join(' ');
  const total = analyzeText(allText);
  console.log('');
  if (total.puaCount === 0) {
    console.log('%c  ★ 结论：DOM 里的文本【完全干净】，无任何私用区字符',
                'color:#16a34a;font-weight:bold');
    console.log('%c    → 注入方案有价值：书名等字段可直接使用，无需解码',
                'color:#16a34a');
  } else {
    console.log('%c  ★ 结论：DOM 里文本含私用区字符',
                'color:#d97706;font-weight:bold');
    console.log(`     共 ${total.puaCount} 个（占 ${total.puaRatio}%）`);
    console.log('     样例码位：' + total.puaSamples.join(', '));
    console.log('%c    → 说明 DOM 也是编码层，注入相比直连没有字符层面的优势',
                'color:#d97706');
  }

  // ============================================================
  //  第 3 步：检查全局状态与 DOM 的书本数
  // ============================================================
  log('');
  log('【3/4】全局状态与当前书数');

  let stateBookCount = 0;
  try {
    const st = window.__INITIAL_STATE__;
    if (st && st.rank && Array.isArray(st.rank.book_list)) {
      stateBookCount = st.rank.book_list.length;
      console.log('window.__INITIAL_STATE__.rank.book_list 有 ' + stateBookCount + ' 本');
      console.log('  total_num = ' + st.rank.total_num);
      console.log('  defaultPage = ' + st.rank.defaultPage);

      // 分析全局状态里的书名
      const first = st.rank.book_list[0];
      if (first && first.bookName) {
        const a = analyzeText(String(first.bookName));
        console.log('  全局状态里的书名（第一本）：');
        console.log('    原始：' + a.text);
        console.log('    显示：' + show(a.text));
        console.log('    私用区字符：' + a.puaCount + ' 个');
      }
    } else {
      console.log('未找到 __INITIAL_STATE__.rank.book_list');
    }
  } catch (e) {
    console.log('读全局状态失败：' + e.message);
  }

  const domCount = listEl.querySelectorAll('[class*="book-item"], li').length;
  console.log('DOM 里的条目数：' + domCount);

  // ============================================================
  //  第 4 步：滚动测试（是否加载更多）
  // ============================================================
  log('');
  log('【4/4】滚动加载测试（会滚动 3 次，请稍候）');

  const countBooks = () => listEl.querySelectorAll('[class*="book-item"], li').length;

  const before = countBooks();
  console.log('滚动前：' + before + ' 本');

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  for (let i = 1; i <= 3; i++) {
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(2000);
    const now = countBooks();
    console.log(`第 ${i} 次滚动后：${now} 本（${now > before ? '增加了' : '没变'}）`);
  }

  const after = countBooks();
  console.log('');
  if (after > before) {
    console.log('%c  ★ 滚动会加载更多，注入可采到超出首屏的数据',
                'color:#16a34a;font-weight:bold');
    console.log(`     从 ${before} 本增至 ${after} 本`);
  } else {
    console.log('%c  ★ 滚动没有加载更多（可能需要点「加载更多」按钮，或该页本就只有这些）',
                'color:#d97706;font-weight:bold');
  }

  // 滚动后重新分析一次，看新加载的内容是否也干净
  if (after > before) {
    const walker2 = document.createTreeWalker(listEl, NodeFilter.SHOW_TEXT);
    let t2 = '';
    let n2;
    while ((n2 = walker2.nextNode())) {
      t2 += (n2.nodeValue || '');
    }
    const a2 = analyzeText(t2);
    console.log('');
    console.log('滚动加载后的整体分析：');
    console.log('  文本总长：' + a2.length);
    console.log('  私用区字符：' + a2.puaCount + ' 个（' + a2.puaRatio + '%）');
  }

  // ============================================================
  //  汇总
  // ============================================================
  console.log('');
  console.log('%c══════════════════════════════════════════════', 'color:#2563eb');
  log('验证结束，请把以上全部输出复制发我');
  console.log('%c══════════════════════════════════════════════', 'color:#2563eb');
  console.log('');
  console.log('重点看这三行：');
  console.log('  1. 【2/4】的「★ 结论」—— DOM 里是否含私用区字符');
  console.log('  2. 【2/4】的文本片段表 —— 书名在第几段、占多少乱码');
  console.log('  3. 【4/4】的「★」—— 滚动是否加载更多');
})();
