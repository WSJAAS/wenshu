// ==UserScript==
// @name         文枢 · 番茄联动
// @namespace    http://127.0.0.1:8010/
// @version      2.0.0
// @description  在番茄小说页面发起取文：由浏览器在站内逐章取回正文并交给本地文枢。登录态由浏览器自动携带，不需要交出 Cookie。
// @author       文枢
// @match        https://fanqienovel.com/*
// @match        https://*.fanqienovel.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

/*
 * 这个脚本做什么
 *
 *   1. 读取当前页面的书籍信息（详情页 / 阅读页都能认）
 *   2. 把书交给本机文枢，建一个取源任务
 *   3. **在番茄页面内逐章取正文**，把原始 HTML 回传本机
 *
 * 为什么要在页面里取，而不是让后端直接抓
 *
 *   番茄的登录凭据 sessionid 是 HttpOnly Cookie。
 *   - 页内 JS 读不到（document.cookie 拿不到 HttpOnly）
 *   - 后端也拿不到（没有凭据）
 *   但**浏览器自己**在 fanqienovel.com 源内发请求时，会由浏览器
 *   自动带上这些 HttpOnly Cookie。
 *   所以让浏览器去取，是最省事的做法：不需要把凭据交出来，
 *   也不需要逆风向控签名 —— 就是一次普通的站内页面请求。
 *
 * 数据流向
 *
 *   番茄页面 → 本机 127.0.0.1:8010
 *   不回传任何 Cookie。登录态始终留在浏览器里。
 *
 * 怎么用
 *
 *   1. 在番茄小说**登录**你的账号（关键：不登录只能取到前 10 章）
 *   2. 打开任意书籍详情页或阅读页
 *   3. 点右下角「交给文枢」，等进度跑完
 *   4. 回到文枢查看结果
 */

(function () {
  'use strict';

  // ---------- 配置 ----------
  const DEFAULT_ENDPOINT = 'http://127.0.0.1:8010';
  const endpoint = GM_getValue('wenshu_endpoint', DEFAULT_ENDPOINT);

  // 逐章取文的间隔（毫秒）。取太快容易被风控，取太慢整本要等很久。
  // 默认 1.2 秒，与后端直连抓取的保守值保持一致。
  const CHAPTER_INTERVAL = Number(GM_getValue('wenshu_interval', 1200));
  // 一次交接最多取多少章，防止误点拉整本
  const MAX_CHAPTERS = Number(GM_getValue('wenshu_max_chapters', 60));
  // 每攒多少章回传一次后端
  const BATCH_SIZE = 5;

  const log = (...a) => console.log('%c[文枢]', 'color:#2563eb;font-weight:bold', ...a);
  const warn = (...a) => console.warn('%c[文枢]', 'color:#d97706;font-weight:bold', ...a);

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- 从页面识别书籍 ----------
  function detectBook() {
    let bookId = '';
    let itemId = '';

    const mPage = location.pathname.match(/\/page\/(\d+)/);
    if (mPage) bookId = mPage[1];

    const mReader = location.pathname.match(/\/reader\/(\d+)/);
    if (mReader) itemId = mReader[1];

    let title = '', author = '', chapterTotal = 0;
    try {
      const st = window.__INITIAL_STATE__;
      const page = st && st.page;
      if (page) {
        if (page.bookId) bookId = String(page.bookId);
        title = String(page.bookName || '');
        author = String(page.author || page.authorName || '');
        chapterTotal = Number(page.chapterTotal || 0) || 0;
      }
      const reader = st && st.reader;
      if (!title && reader && reader.bookInfo) {
        const bi = reader.bookInfo;
        bookId = String(bi.bookId || bookId);
        title = String(bi.bookName || '');
        author = String(bi.author || '');
      }
    } catch (e) { /* 读不到就退回 DOM */ }

    if (!title) {
      const h = document.querySelector('h1, .info-name, [class*="book-name"], [class*="bookName"]');
      if (h) title = (h.textContent || '').trim().slice(0, 100);
    }
    if (!title) {
      const og = document.querySelector('meta[property="og:title"], meta[name="title"]');
      if (og) title = (og.getAttribute('content') || '').trim().slice(0, 100);
    }

    return { bookId, itemId, title, author, chapterTotal };
  }

  // ---------- 请求本机服务 ----------
  function callLocal(method, path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: endpoint + path,
        headers: { 'Content-Type': 'application/json' },
        data: payload ? JSON.stringify(payload) : undefined,
        timeout: 60000,
        onload: (r) => {
          if (r.status >= 200 && r.status < 300) {
            try { resolve(JSON.parse(r.responseText)); }
            catch { resolve({ raw: r.responseText }); }
          } else {
            let detail = r.responseText;
            try { detail = JSON.parse(r.responseText).detail || detail; } catch { /* 原样 */ }
            reject(new Error(`HTTP ${r.status}: ${String(detail).slice(0, 200)}`));
          }
        },
        onerror: () => reject(new Error('连不上本机文枢。请确认服务在运行（127.0.0.1:8010）')),
        ontimeout: () => reject(new Error('请求本机服务超时')),
      });
    });
  }

  // ============================================================
  //  核心：在页面内取一章正文
  //
  //  用普通 fetch 发同源请求。浏览器会自动带上该域下的
  //  HttpOnly Cookie（含 sessionid），这正是我们拿不到、但
  //  浏览器自己能用的那一份登录态。
  //
  //  返回原始 HTML 文本，解码交给后端统一处理 ——
  //  字体码表在后端，前端不重复实现一遍。
  // ============================================================
  async function fetchChapterHtml(itemId) {
    const url = `/reader/${itemId}`;
    const resp = await fetch(url, {
      method: 'GET',
      credentials: 'include',   // 关键：带上同源 Cookie
      headers: { 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8' },
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    return await resp.text();
  }

  // 从 HTML 里粗略判断这一章是不是拿到了全文（仅用于进度显示，
  // 最终判定以后端为准）
  function quickPeek(html) {
    const m = html.match(/"content":"/);
    if (!m) return { ok: false, note: '无正文' };
    const i = html.indexOf('"content":"');
    const seg = html.slice(i, i + 4000);
    const locked = /"isChapterLock":true/.test(html);
    return { ok: true, locked };
  }

  // ---------- 进度面板 ----------
  function makePanel() {
    let el = document.getElementById('wenshu-progress');
    if (el) return el;

    el = document.createElement('div');
    el.id = 'wenshu-progress';
    el.style.cssText = [
      'position:fixed', 'right:24px', 'bottom:160px', 'z-index:2147483000',
      'width:300px', 'background:rgba(17,24,39,.94)', 'color:#fff',
      'border-radius:12px', 'padding:14px 16px', 'font-size:13px',
      'font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif',
      'box-shadow:0 8px 28px rgba(0,0,0,.4)', 'line-height:1.6',
    ].join(';');
    document.body.appendChild(el);
    return el;
  }

  function updatePanel(lines) {
    const el = makePanel();
    el.innerHTML = lines.map((l) => `<div>${l}</div>`).join('');
  }

  function removePanel() {
    const el = document.getElementById('wenshu-progress');
    if (el) el.remove();
  }

  // ---------- 交接并取文 ----------
  let running = false;

  async function handOver(btn) {
    if (running) {
      alert('正在取文中，请稍候。');
      return;
    }

    const book = detectBook();
    if (!book.bookId && !book.itemId) {
      alert('没能识别出书籍。请在书籍详情页或阅读页使用。');
      return;
    }

    const original = btn.textContent;
    running = true;
    btn.disabled = true;
    btn.textContent = '取文中…';

    try {
      // 1) 把书交给文枢，建任务
      //
      // fetch_mode='browser' 很关键：只建任务，不让后端直连抓。
      // 否则同一批章会被抓两遍（白耗配额、更易触发风控），
      // 而且直连拿到的试读结果会先落库、再被下面的结果覆盖。
      updatePanel(['<b>正在建立任务…</b>']);
      const handover = await callLocal('POST', '/api/source/handover', {
        book_id: book.bookId || '',
        item_id: book.itemId || '',
        title: book.title,
        author: book.author,
        chapter_total: book.chapterTotal,
        source_url: location.href,
        create_task: true,
        fetch_mode: 'browser',
        chapter_count: MAX_CHAPTERS,
        auto_import: true,
      });

      const taskId = handover.task_id;
      if (!taskId) {
        throw new Error('后端未返回任务 ID：' + JSON.stringify(handover).slice(0, 200));
      }
      log('任务已建立', taskId, handover);

      // 2) 取清单
      const plan = await callLocal(
        'GET',
        `/api/source/tasks/${encodeURIComponent(taskId)}/browser-plan?only_missing=true`
      );

      const items = plan.items || [];
      if (items.length === 0) {
        updatePanel(['<b>没有需要补取的章节。</b>', '正文已经齐了。']);
        setTimeout(removePanel, 4000);
        alert('这本书当前没有需要补取的章节，正文已经齐了。');
        return;
      }

      // 3) 逐章取，攒够一批回传
      let sent = 0;
      let okCount = 0;
      let previewCount = 0;
      let failed = 0;
      let buffer = [];

      const flush = async () => {
        if (buffer.length === 0) return;
        const payload = { task_id: taskId, chapters: buffer };
        buffer = [];
        const res = await callLocal('POST', '/api/source/browser-chapters', payload);
        okCount += res.accepted_ok || 0;
        previewCount += res.accepted_preview || 0;
        sent = res.done || sent;
      };

      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        updatePanel([
          `<b>${plan.book_title || '取文中'}</b>`,
          `进度：${i + 1} / ${items.length} 章`,
          `完整 ${okCount} · 试读 ${previewCount} · 失败 ${failed}`,
          `<span style="opacity:.7">第 ${it.order} 章</span>`,
        ]);

        try {
          const html = await fetchChapterHtml(it.item_id);
          buffer.push({ item_id: it.item_id, html });
        } catch (e) {
          failed++;
          warn('取章失败', it.item_id, e.message);
        }

        if (buffer.length >= BATCH_SIZE) {
          try {
            await flush();
          } catch (e) {
            warn('回传失败', e.message);
            // 回传失败不丢数据 —— 保留 buffer 下轮再试
          }
        }

        if (i < items.length - 1) {
          await sleep(CHAPTER_INTERVAL);
        }
      }

      // 收尾
      try { await flush(); } catch (e) { warn('收尾回传失败', e.message); }

      updatePanel([
        '<b>取文完成</b>',
        `完整 ${okCount} 章 · 试读 ${previewCount} 章 · 失败 ${failed} 章`,
        '<span style="opacity:.7">回到文枢查看</span>',
      ]);
      setTimeout(removePanel, 6000);

      alert(
        '已交给文枢。\n\n' +
        `书籍：${plan.book_title || book.title || book.bookId}\n` +
        `本次取回：完整 ${okCount} 章` +
        (previewCount ? `，试读 ${previewCount} 章` : '') +
        (failed ? `，失败 ${failed} 章` : '') + '\n\n' +
        '可以回到文枢的「在线书源」页面查看结果。'
      );
    } catch (e) {
      warn('交接失败', e);
      updatePanel(['<b style="color:#fca5a5">失败</b>', String(e.message).slice(0, 200)]);
      setTimeout(removePanel, 6000);
      alert('交接失败：' + e.message);
    } finally {
      running = false;
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  // ---------- 注入按钮 ----------
  function injectButton() {
    if (document.getElementById('wenshu-handover-btn')) return;

    const book = detectBook();
    if (!book.bookId && !book.itemId) return;

    const box = document.createElement('div');
    box.id = 'wenshu-handover-btn';
    box.style.cssText = [
      'position:fixed', 'right:24px', 'bottom:96px', 'z-index:2147483000',
      'display:flex', 'flex-direction:column', 'gap:6px', 'align-items:flex-end',
      'font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif',
    ].join(';');

    const tip = document.createElement('div');
    tip.style.cssText = [
      'background:rgba(0,0,0,.72)', 'color:#fff', 'font-size:12px',
      'padding:6px 10px', 'border-radius:6px', 'max-width:270px',
      'line-height:1.5', 'display:none',
    ].join(';');

    const btn = document.createElement('button');
    btn.textContent = '交给文枢';
    btn.style.cssText = [
      'background:#2563eb', 'color:#fff', 'border:none', 'border-radius:20px',
      'padding:10px 18px', 'font-size:14px', 'font-weight:600', 'cursor:pointer',
      'box-shadow:0 2px 10px rgba(37,99,235,.4)', 'transition:transform .12s',
    ].join(';');

    btn.onmouseenter = () => { if (!running) btn.style.transform = 'translateY(-1px)'; };
    btn.onmouseleave = () => { btn.style.transform = 'none'; };
    btn.onclick = () => void handOver(btn);

    box.onmouseenter = async () => {
      const b = detectBook();
      tip.textContent =
        `将取文：${b.title || b.bookId || b.itemId}` +
        (b.chapterTotal ? `（共 ${b.chapterTotal} 章）` : '') +
        `\n本次最多取 ${MAX_CHAPTERS} 章，由浏览器站内取文`;
      tip.style.display = 'block';
    };
    box.onmouseleave = () => { tip.style.display = 'none'; };

    box.appendChild(tip);
    box.appendChild(btn);
    document.body.appendChild(box);
    log('已注入按钮');
  }

  // ---------- 菜单命令 ----------
  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('交给文枢（取当前书）', () => {
      const btn = document.querySelector('#wenshu-handover-btn button');
      if (btn) void handOver(btn);
      else alert('当前页面不是书籍页。');
    });
    GM_registerMenuCommand('设置文枢地址', () => {
      const v = prompt('文枢地址（默认 http://127.0.0.1:8010）', endpoint);
      if (v && v.trim()) {
        GM_setValue('wenshu_endpoint', v.trim().replace(/\/+$/, ''));
        alert('已保存，刷新页面生效。');
      }
    });
    GM_registerMenuCommand('设置最大取文章数', () => {
      const v = prompt('一次交接最多取多少章（默认 60）', String(MAX_CHAPTERS));
      const n = Number(v);
      if (n > 0) {
        GM_setValue('wenshu_max_chapters', n);
        alert('已保存，刷新页面生效。');
      }
    });
    GM_registerMenuCommand('设置取文间隔（毫秒）', () => {
      const v = prompt('逐章间隔毫秒（默认 1200，太小易触发风控）', String(CHAPTER_INTERVAL));
      const n = Number(v);
      if (n >= 300) {
        GM_setValue('wenshu_interval', n);
        alert('已保存，刷新页面生效。');
      } else {
        alert('间隔不能小于 300 毫秒。');
      }
    });
  }

  // ---------- 启动 ----------
  let lastUrl = location.href;
  function tick() {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      const old = document.getElementById('wenshu-handover-btn');
      if (old) old.remove();
    }
    injectButton();
  }

  injectButton();
  setInterval(tick, 1500);
  log('脚本已加载（v2 页内取文）。地址：' + endpoint);
})();
