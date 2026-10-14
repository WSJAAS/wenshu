// ==UserScript==
// @name         文枢 · 番茄联动
// @namespace    http://127.0.0.1:8010/
// @version      1.0.0
// @description  在番茄小说页面选书，一键把书与登录态交给本地文枢。Cookie 只发往你自己电脑上的 127.0.0.1。
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
 * 这个脚本做三件事：
 *
 *   1. 读取当前页面的书籍信息（详情页/阅读页都能认）
 *   2. 把 Cookie 和书一起发给你本机的文枢
 *   3. 在页面上给一个按钮，点一下就交接
 *
 * 为什么需要它：
 *   番茄对未登录访问只开放每本书前 10 章全文，第 11 章起截断为约 200 字符
 *   的试读片段。要拿全文必须带登录态，而登录态只存在于你的浏览器里。
 *   这个脚本负责把它取出来交给本机服务。
 *
 * 数据流向：番茄页面 → 本机 127.0.0.1:8010。不经过任何第三方。
 *
 * Cookie 只保存在你本机的数据目录，不入库、不进 git。
 */

(function () {
  'use strict';

  // ---------- 配置 ----------
  const DEFAULT_ENDPOINT = 'http://127.0.0.1:8010';
  const endpoint = GM_getValue('wenshu_endpoint', DEFAULT_ENDPOINT);

  const log = (...a) => console.log('%c[文枢]', 'color:#2563eb;font-weight:bold', ...a);
  const warn = (...a) => console.warn('%c[文枢]', 'color:#d97706;font-weight:bold', ...a);

  // ---------- 从页面识别书籍 ----------
  function detectBook() {
    // 方式 1：从 URL 取 bookId
    //   详情页 /page/<bookId>
    //   阅读页 /reader/<itemId>（需要再查所属书）
    let bookId = '';
    let itemId = '';

    const mPage = location.pathname.match(/\/page\/(\d+)/);
    if (mPage) bookId = mPage[1];

    const mReader = location.pathname.match(/\/reader\/(\d+)/);
    if (mReader) itemId = mReader[1];

    // 方式 2：从全局状态取（最可靠）
    let title = '', author = '', chapterTotal = 0, fromState = false;
    try {
      const st = window.__INITIAL_STATE__;
      const page = st && st.page;
      if (page) {
        if (page.bookId) bookId = String(page.bookId);
        title = String(page.bookName || '');
        author = String(page.author || page.authorName || '');
        chapterTotal = Number(page.chapterTotal || 0) || 0;
        fromState = true;
      }
      // 阅读页的书信息在 reader 段
      const reader = st && st.reader;
      if (!title && reader && reader.bookInfo) {
        const bi = reader.bookInfo;
        bookId = String(bi.bookId || bookId);
        title = String(bi.bookName || '');
        author = String(bi.author || '');
      }
    } catch (e) {
      // 读不到全局状态就退回 DOM
    }

    // 方式 3：从 DOM 兜底
    if (!title) {
      const h = document.querySelector('h1, .info-name, [class*="book-name"], [class*="bookName"]');
      if (h) title = (h.textContent || '').trim().slice(0, 100);
    }

    // 方式 4：从 meta 兜底
    if (!title) {
      const og = document.querySelector('meta[property="og:title"], meta[name="title"]');
      if (og) title = (og.getAttribute('content') || '').trim().slice(0, 100);
    }

    return { bookId, itemId, title, author, chapterTotal, fromState };
  }

  // ---------- 请求本机服务 ----------
  function callLocal(method, path, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: endpoint + path,
        headers: { 'Content-Type': 'application/json' },
        data: payload ? JSON.stringify(payload) : undefined,
        timeout: 15000,
        onload: (r) => {
          if (r.status >= 200 && r.status < 300) {
            try {
              resolve(JSON.parse(r.responseText));
            } catch {
              resolve({ raw: r.responseText });
            }
          } else {
            let detail = r.responseText;
            try { detail = JSON.parse(r.responseText).detail || detail; } catch { /* 原样 */ }
            reject(new Error(`HTTP ${r.status}: ${String(detail).slice(0, 200)}`));
          }
        },
        onerror: () => reject(new Error('连不上本机文枢。请确认服务在运行（127.0.0.1:8010）')),
        ontimeout: () => reject(new Error('请求超时')),
      });
    });
  }

  // ---------- 状态检查 ----------
  async function checkHealth() {
    try {
      const r = await callLocal('GET', '/health');
      return r && r.status === 'ok';
    } catch {
      return false;
    }
  }

  // ---------- 交接 ----------
  async function handOver(btn) {
    const book = detectBook();

    if (!book.bookId && !book.itemId) {
      alert('没能识别出书籍。请在书籍详情页或阅读页使用。');
      return;
    }

    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '交接中…';

    try {
      // 1) 先交付登录态（每个番茄域下的 Cookie）
      const cookie = document.cookie || '';
      let loginMsg = '';
      if (cookie) {
        try {
          const r = await callLocal('POST', '/api/source/login', { cookie });
          loginMsg = r.has_sessionid
            ? '登录态已就绪，可取得全文'
            : 'Cookie 已交付，但未发现 sessionid —— 可能仍未登录';
        } catch (e) {
          loginMsg = '登录态交付失败：' + e.message;
        }
      } else {
        loginMsg = '页面未提供 Cookie（可能未登录或均为 HttpOnly）';
      }

      // 2) 把书交给文枢建取源任务
      const payload = {
        book_id: book.bookId || '',
        item_id: book.itemId || '',
        title: book.title,
        author: book.author,
        chapter_total: book.chapterTotal,
        source_url: location.href,
      };

      const r = await callLocal('POST', '/api/source/handover', payload);

      log('交接完成', r);
      alert(
        '已交给文枢。\n\n' +
        '书籍：' + (book.title || book.bookId || book.itemId) + '\n' +
        (book.author ? '作者：' + book.author + '\n' : '') +
        '登录态：' + loginMsg + '\n\n' +
        (r.message || '') +
        (r.task_id ? '\n任务：' + r.task_id.slice(0, 8) + '…' : '') +
        '\n\n可以回到文枢查看了。'
      );
    } catch (e) {
      warn('交接失败', e);
      alert('交接失败：' + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  // ---------- 注入按钮 ----------
  function injectButton() {
    if (document.getElementById('wenshu-handover-btn')) return;

    const book = detectBook();
    if (!book.bookId && !book.itemId) return;   // 非书籍页面不注入

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
      'padding:6px 10px', 'border-radius:6px', 'max-width:260px',
      'line-height:1.5', 'display:none',
    ].join(';');

    const btn = document.createElement('button');
    btn.textContent = '交给文枢';
    btn.style.cssText = [
      'background:#2563eb', 'color:#fff', 'border:none', 'border-radius:20px',
      'padding:10px 18px', 'font-size:14px', 'font-weight:600', 'cursor:pointer',
      'box-shadow:0 2px 10px rgba(37,99,235,.4)', 'transition:transform .12s',
    ].join(';');

    btn.onmouseenter = () => { btn.style.transform = 'translateY(-1px)'; };
    btn.onmouseleave = () => { btn.style.transform = 'none'; };
    btn.onclick = () => void handOver(btn);

    // 悬停显示书名，避免用户点错书
    box.onmouseenter = async () => {
      const b = detectBook();
      const ok = await checkHealth();
      tip.textContent = ok
        ? `将交接：${b.title || b.bookId || b.itemId}` +
          (b.chapterTotal ? `（${b.chapterTotal} 章）` : '')
        : '连不上本机文枢，请先启动服务';
      tip.style.display = 'block';
      if (!ok) {
        btn.style.background = '#9ca3af';
        btn.style.cursor = 'not-allowed';
      } else {
        btn.style.background = '#2563eb';
        btn.style.cursor = 'pointer';
      }
    };
    box.onmouseleave = () => { tip.style.display = 'none'; };

    box.appendChild(tip);
    box.appendChild(btn);
    document.body.appendChild(box);
    log('已注入交接按钮');
  }

  // ---------- 菜单命令 ----------
  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('交接当前书给文枢', () => {
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
  }

  // ---------- 启动 ----------
  // 番茄是 SPA，切页不会重载，所以监听路由变化
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
  log('脚本已加载。地址：' + endpoint);
})();
