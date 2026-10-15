import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Drawer,
  Empty,
  Grid,
  Input,
  InputNumber,
  List,
  message,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Segmented,
  Select,
  Skeleton,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
  theme,
} from 'antd';
import {
  CloudDownloadOutlined,
  ReloadOutlined,
  SaveOutlined,
  StopOutlined,
  TrophyOutlined,
  ImportOutlined,
  ArrowLeftOutlined,
  BookOutlined,
  ReadOutlined,
  RightOutlined,
  SearchOutlined,
  InfoCircleOutlined,
  WarningOutlined,
  LinkOutlined,
} from '@ant-design/icons';
import { sourceApi, bookImportApi, extDownloadApi } from '../services/api';
import type {
  BookImportTask,
  ExtDownloadJob,
  ExtDownloadStatus,
  SourceBookResponse,
  SourceChapterResult,
  SourceHandoverGuide,
  SourceLoginState,
  SourceRankItem,
  SourceRankResponse,
  SourceSelection,
  SourceTask,
  SourceTaskChaptersResponse,
} from '../types';
import './Source.css';

const { Text, Title, Paragraph } = Typography;

/**
 * 番茄站点的入口地址。
 *
 * 写死在这里而不是从后端取：这两个地址是平台公开的常规入口，
 * 不需要随抓取逻辑变化；而且按钮要在接口失败时也能用 ——
 * 后端连不上时，用户更需要能自己打开番茄去看看。
 */
const FANQIE_HOME = 'https://fanqienovel.com/';
const FANQIE_RANK = 'https://fanqienovel.com/rank';

/** 逐章状态的展示样式 */
const STATUS_META: Record<string, { color: string; label: string }> = {
  ok: { color: 'success', label: '完整' },
  // preview 必须与 ok 分开展示。未登录时锁定章只能拿到被截断的开头，
  // 若标成「成功」，界面会显示 550/550 而实际只覆盖很小一部分。
  preview: { color: 'warning', label: '仅试读片段' },
  locked: { color: 'warning', label: '锁定' },
  unavailable: { color: 'default', label: '暂不可用' },
  garbled: { color: 'error', label: '乱码' },
  failed: { color: 'error', label: '失败' },
  not_fetched: { color: 'default', label: '未抓取' },
  skipped: { color: 'default', label: '已跳过' },
};

const TASK_STATUS_META: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '等待中' },
  running: { color: 'processing', label: '抓取中' },
  paused: { color: 'warning', label: '已暂停' },
  completed: { color: 'success', label: '已完成' },
  failed: { color: 'error', label: '失败' },
  cancelled: { color: 'default', label: '已取消' },
};

/** 拆书模块的任务状态文案 */
const BOOK_IMPORT_STATUS_LABEL: Record<string, string> = {
  pending: '等待处理',
  running: '处理中',
  completed: '可预览导入',
  failed: '失败',
  cancelled: '已取消',
};

/** 外部下载器（整本下载）的任务状态文案 */
const EXT_STATE_META: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: '排队中' },
  running: { color: 'processing', label: '下载中' },
  done: { color: 'success', label: '已完成' },
  error: { color: 'error', label: '失败' },
};

/** 终态：到了这里就不用再轮询 */
const EXT_TERMINAL_STATES = ['done', 'error'];

/**
 * 把 status 响应归一到 ExtDownloadStatus。
 *
 * 约定的形状是 `{ job: { id, state, ... }, epub_path, epub_bytes }`；
 * 但状态接口也可能把 job 的字段摊在顶层（`state` / `progress` / `title`
 * 直接挂在根上）。两种都认，免得界面上的进度永远空着。
 */
function normalizeExtStatus(raw: unknown): ExtDownloadStatus | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Partial<ExtDownloadStatus> & Partial<ExtDownloadJob> & { job_id?: number };

  if (data.job && typeof data.job === 'object') {
    return data as ExtDownloadStatus;
  }
  if (typeof data.state !== 'string') return null;

  return {
    job: {
      // 摊平形状里任务号在根上叫 job_id
      id: typeof data.id === 'number' ? data.id : data.job_id,
      state: data.state,
      title: data.title,
      author: data.author,
      error: data.error,
      progress: data.progress,
    },
    epub_path: data.epub_path ?? null,
    epub_bytes: data.epub_bytes ?? null,
  };
}

/**
 * 从「书籍 ID 或链接」里取出数字 book_id。
 *
 * 与后端 _normalize_book_id 同一套规则：链接形式
 * https://fanqienovel.com/page/7231424472606051384 取 page / reader 后的那一段；
 * 认不出来时退回「最长的一段数字」—— 平台 ID 有 19 位，
 * 分享文本里出现的其它数字（时间戳、序号）都不会比它更长。
 */
function parseBookId(raw: string): string {
  const text = (raw || '').trim();
  if (!text) return '';

  if (text.includes('/')) {
    const parts = text.split('/').filter(Boolean);
    const index = parts.findIndex((part) => part === 'page' || part === 'reader');
    const candidate = (index >= 0 && index + 1 < parts.length ? parts[index + 1] : parts[parts.length - 1])
      .split('?')[0]
      .split('#')[0]
      .trim();
    if (/^\d+$/.test(candidate)) return candidate;
  }

  if (/^\d+$/.test(text.split('?')[0].trim())) return text.split('?')[0].trim();

  const matches = text.match(/\d{5,}/g);
  if (!matches || !matches.length) return '';
  return matches.reduce((longest, current) => (
    current.length > longest.length ? current : longest
  ));
}

function fmtCount(n: number | undefined): string {
  if (!n) return '0';
  if (n >= 100000000) return `${(n / 100000000).toFixed(2)}亿`;
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return n.toLocaleString();
}

function BookCover({ src, title, large = false }: { src?: string; title: string; large?: boolean }) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  return (
    <span className={`source-book-cover${large ? ' source-book-cover-large' : ''}`}>
      {src && src !== failedSrc ? (
        <img
          src={src}
          alt={`${title}封面`}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailedSrc(src)}
        />
      ) : (
        <span className="source-cover-placeholder" role="img" aria-label={`${title}暂无封面`}>
          <BookOutlined />
          {large && <span>{title}</span>}
        </span>
      )}
    </span>
  );
}

export default function Source() {
  const { token } = theme.useToken();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const navigate = useNavigate();
  const [msgApi, ctxHolder] = message.useMessage();

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [statusNote, setStatusNote] = useState('');

  // 登录态。未登录时锁定章只有约 200 字符的试读片段 ——
  // 一本 550 章的书只能完整取到前 10 章，必须让用户看见这件事。
  const [login, setLogin] = useState<SourceLoginState | null>(null);
  const [loginInput, setLoginInput] = useState('');
  const [savingLogin, setSavingLogin] = useState(false);

  const [rank, setRank] = useState<SourceRankResponse | null>(null);
  const [loadingRank, setLoadingRank] = useState(false);
  const [rankError, setRankError] = useState(false);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [bookStatus, setBookStatus] = useState('全部');

  const [selectedBook, setSelectedBook] = useState<SourceRankItem | null>(null);
  const [book, setBook] = useState<SourceBookResponse | null>(null);
  const [loadingBook, setLoadingBook] = useState(false);
  const [bookError, setBookError] = useState(false);
  const bookRequest = useRef(0);
  const rankRequest = useRef(0);
  const bookTrigger = useRef<HTMLElement | null>(null);

  const [preset, setPreset] = useState<'first' | 'volume' | 'all'>('first');
  const [count, setCount] = useState(12);
  const [volume, setVolume] = useState('');
  const [interval, setInterval] = useState(1.2);

  const [task, setTask] = useState<SourceTask | null>(null);
  const [chapters, setChapters] = useState<SourceTaskChaptersResponse | null>(null);
  const [tasks, setTasks] = useState<SourceTask[]>([]);

  /** 浏览器联动的安装指引。拿不到就不显示这一块。 */
  const [guide, setGuide] = useState<SourceHandoverGuide | null>(null);

  /** 交接后拆书模块那边的任务状态。用于显示对方的反应。 */
  const [importTask, setImportTask] = useState<BookImportTask | null>(null);

  // ---- 整本下载（外部下载器）----
  const [extInput, setExtInput] = useState('');
  const [extStarting, setExtStarting] = useState(false);
  const [extJobId, setExtJobId] = useState<number | null>(null);
  const [extStatus, setExtStatus] = useState<ExtDownloadStatus | null>(null);
  const [extError, setExtError] = useState('');
  /** 每发起一次任务自增。job_id 偶然重复时也能让轮询重新起跑。 */
  const [extRunSeq, setExtRunSeq] = useState(0);
  /** 轮询是否还在跑。用它挡住重复点击，并保证卸载后不再 setState。 */
  const extPolling = useRef(false);

  const categories = useMemo(
    () => [...new Set((rank?.items ?? []).map((item) => item.category).filter((value): value is string => !!value))],
    [rank],
  );
  const visibleBooks = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    return (rank?.items ?? []).map((item, index) => ({ item, position: index + 1 })).filter(({ item }) => (
      (!keyword || `${item.title} ${item.author}`.toLocaleLowerCase().includes(keyword))
      && (!category || item.category === category)
      && (bookStatus === '全部' || item.status?.includes(bookStatus))
    ));
  }, [rank, query, category, bookStatus]);
  const preview = book?.book ?? selectedBook;
  const sourceStyle = {
    '--source-bg': token.colorBgContainer,
    '--source-text': token.colorText,
    '--source-muted': token.colorTextSecondary,
    '--source-border': token.colorBorderSecondary,
    '--source-accent': token.colorPrimary,
    '--source-tint': token.colorPrimaryBg,
    '--source-hover': token.colorFillQuaternary,
  } as CSSProperties;

  useEffect(() => () => {
    bookRequest.current += 1;
    rankRequest.current += 1;
    // 卸载后立刻停掉整本下载的轮询，避免继续打后端。
    extPolling.current = false;
  }, []);

  // ---- 模块状态 ----
  useEffect(() => {
    sourceApi.status()
      .then((s) => {
        setEnabled(s.enabled);
        setStatusNote(s.note);
      })
      .catch(() => setEnabled(false));
  }, []);

  // ---- 登录态 ----
  const loadLogin = useCallback(async () => {
    try {
      setLogin(await sourceApi.loginState());
    } catch {
      setLogin(null);
    }
  }, []);

  useEffect(() => {
    if (enabled) void loadLogin();
  }, [enabled, loadLogin]);

  // ---- 浏览器联动指引 ----
  const loadGuide = useCallback(async () => {
    try {
      setGuide(await sourceApi.handoverGuide());
    } catch {
      /* 拿不到指引就不显示这一块 */
    }
  }, []);

  useEffect(() => {
    if (enabled) void loadGuide();
  }, [enabled, loadGuide]);

  const saveLogin = useCallback(async () => {
    setSavingLogin(true);
    try {
      const state = await sourceApi.setLogin(loginInput.trim());
      setLogin(state);
      setLoginInput('');
      msgApi.success(state.configured ? '登录态已保存' : '登录态已清除');
    } catch {
      msgApi.error('保存失败');
    } finally {
      setSavingLogin(false);
    }
  }, [loginInput, msgApi]);

  const loadTasks = useCallback(async () => {
    try {
      const t = await sourceApi.tasks(20);
      setTasks(t);
    } catch {
      /* 列表失败不打扰用户 */
    }
  }, []);

  useEffect(() => {
    if (enabled) loadTasks();
  }, [enabled, loadTasks]);

  // ---- 榜单 ----
  const loadRank = useCallback(async () => {
    const request = ++rankRequest.current;
    setLoadingRank(true);
    setRankError(false);
    try {
      const r = await sourceApi.rank();
      if (request !== rankRequest.current) return;
      setRank(r);
    } catch (e: unknown) {
      if (request !== rankRequest.current) return;
      setRankError(true);
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`读取榜单失败：${err?.response?.data?.detail || err?.message}`);
    } finally {
      if (request === rankRequest.current) setLoadingRank(false);
    }
  }, [msgApi]);

  useEffect(() => {
    if (enabled) loadRank();
  }, [enabled, loadRank]);

  // ---- 详情 ----
  const openBook = async (item: SourceRankItem) => {
    const request = ++bookRequest.current;
    if (!selectedBook) bookTrigger.current = document.activeElement as HTMLElement;
    setSelectedBook(item);
    setLoadingBook(true);
    setBookError(false);
    setBook(null);
    try {
      const b = await sourceApi.book(item.book_id, interval);
      if (request !== bookRequest.current) return;
      setBook(b);
      setVolume(b.volumes[0]?.name ?? '');
    } catch {
      if (request === bookRequest.current) setBookError(true);
    } finally {
      if (request === bookRequest.current) setLoadingBook(false);
    }
  };

  const closeBook = () => {
    bookRequest.current += 1;
    setSelectedBook(null);
    setBook(null);
    setLoadingBook(false);
    setBookError(false);
  };

  // ---- 创建任务 ----
  const createTask = async () => {
    if (!book) return;
    const selection: SourceSelection = {
      preset,
      count,
      volume: preset === 'volume' ? volume : '',
      item_ids: [],
    };
    try {
      const t = await sourceApi.createTask({
        book_id: book.book.book_id,
        selection,
        interval,
      });
      setTask(t);
      setChapters(null);
      closeBook();
      msgApi.success(`已创建取源任务，计划抓取 ${t.total_chapters} 章`);
      loadTasks();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`创建任务失败：${err?.response?.data?.detail || err?.message}`);
    }
  };

  // ---- 任务轮询 ----
  useEffect(() => {
    if (!task) return;
    if (['completed', 'failed', 'cancelled', 'paused'].includes(task.status)) {
      // 终态：拉一次分章结果
      sourceApi.taskChapters(task.task_id)
        .then(setChapters)
        .catch(() => undefined);
      return;
    }

    const tick = window.setInterval(async () => {
      try {
        const t = await sourceApi.task(task.task_id);
        setTask(t);
      } catch {
        /* 轮询失败静默重试 */
      }
    }, 2500);

    return () => window.clearInterval(tick);
  }, [task?.task_id, task?.status]);

  // ---- 拆书任务轮询 ----
  // 交接之后轮询对面的状态，直到跑完。
  // 这是「拆书模块有反应」的可见来源：用户能看到对方在解析、能预览了、还是失败了。
  useEffect(() => {
    const biId = task?.import_task_id;
    if (!biId) {
      setImportTask(null);
      return;
    }
    if (importTask && ['completed', 'failed', 'cancelled'].includes(importTask.status)) {
      return;
    }

    let stopped = false;
    const tick = async () => {
      try {
        const t = await bookImportApi.getTaskStatus(biId);
        if (!stopped) setImportTask(t);
      } catch {
        /* 拿不到就下次再试 */
      }
    };

    void tick();
    const timer = window.setInterval(tick, 3000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [task?.import_task_id, importTask?.status]);

  const cancelTask = async () => {
    if (!task) return;
    try {
      const t = await sourceApi.cancel(task.task_id);
      setTask(t);
      msgApi.info('已请求取消');
    } catch (e: unknown) {
      const err = e as { message?: string };
      msgApi.error(`取消失败：${err?.message}`);
    }
  };

  // ---- 整本下载（外部下载器）----
  // 外部下载器负责整本抓取（含锁定章全文）并把结果打包，
  // 前端只做两件事：起任务、看着它跑完，然后给出 TXT 导出入口。
  const extState = extStatus?.job?.state ?? '';

  const startExtDownload = useCallback(async () => {
    if (extPolling.current) {
      msgApi.warning('已有一个整本下载任务在跑，等它结束再发起');
      return;
    }
    const bookId = parseBookId(extInput);
    if (!bookId) {
      msgApi.error('没识别出书籍 ID。粘贴番茄书籍链接，或直接填数字 ID');
      return;
    }

    setExtStarting(true);
    setExtError('');
    try {
      const r = await extDownloadApi.start(bookId);
      // 先清空上一条任务的进度，否则会短暂显示旧书的章数。
      setExtStatus(null);
      setExtJobId(r.job_id);
      setExtRunSeq((seq) => seq + 1);
      extPolling.current = true;
      msgApi.success(`已交给外部下载器，任务 #${r.job_id}`);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      const detail = err?.response?.data?.detail || err?.message || '未知原因';
      setExtError(detail);
      msgApi.error(`发起整本下载失败：${detail}`);
    } finally {
      setExtStarting(false);
    }
  }, [extInput, msgApi]);

  // 轮询任务状态：每 2s 一次，done / error 即停。
  useEffect(() => {
    if (extJobId === null || !extPolling.current) return;
    let stopped = false;
    let timer = 0;

    const tick = async () => {
      try {
        const s = normalizeExtStatus(await extDownloadApi.getStatus(extJobId));
        if (stopped || !s) return;
        setExtStatus(s);
        const state = s.job?.state;
        if (state && EXT_TERMINAL_STATES.includes(state)) {
          // 先停轮询再提示，避免下一次 tick 又把终态重放一遍。
          stopped = true;
          extPolling.current = false;
          window.clearInterval(timer);
          if (state === 'error') {
            const reason = s.job?.error || '外部下载器未给出原因';
            setExtError(reason);
            msgApi.error(`整本下载失败：${reason}`);
          } else {
            msgApi.success(`《${s.job?.title || '该书'}》整本下载完成，可下载 TXT`);
          }
        }
      } catch {
        /* 单次轮询失败静默重试，任务本身还在跑 */
      }
    };

    timer = window.setInterval(tick, 2000);
    void tick();

    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [extJobId, extRunSeq, msgApi]);

  const downloadExtTxt = () => {
    if (extJobId === null) return;
    window.open(extDownloadApi.resultUrl(extJobId, extStatus?.job?.title), '_blank');
  };

  const handoff = async () => {
    if (!task) return;
    try {
      const r = await sourceApi.toBookImport(task.task_id);
      msgApi.success(
        `已交给拆书导入：${r.handed_chapters} 章，${r.txt_chars} 字。` +
        `拆书任务 ${r.book_import_task?.task_id?.slice(0, 8)}…`
      );
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`交接失败：${err?.response?.data?.detail || err?.message}`);
    }
  };

  const downloadTxt = () => {
    if (!task) return;
    window.open(sourceApi.txtUrl(task.task_id), '_blank');
  };

  /**
   * 打开脚本地址。
   *
   * 直接访问 .user.js 会被浏览器当文件下载；装好油猴后打开同地址
   * 才会弹出安装页。所以这里也给「复制地址」的备选。
   */
  const installScript = () => {
    window.open(sourceApi.userscriptUrl(), '_blank');
  };

  const copyScriptUrl = async () => {
    const url = `${window.location.origin}${sourceApi.userscriptUrl()}`;
    try {
      await navigator.clipboard.writeText(url);
      msgApi.success('脚本地址已复制。装好油猴后打开该地址即可安装。');
    } catch {
      msgApi.info(url);
    }
  };

  // ============================================================
  //  未启用
  // ============================================================
  if (enabled === false) {
    return (
      <div style={{ padding: isMobile ? 12 : 24, maxWidth: 1200, margin: '0 auto' }}>
        {ctxHolder}
        <Title level={4}>在线书源</Title>
        <Alert
          type="info"
          showIcon
          message="书源模块已关闭"
          description={
            <div>
              <Paragraph style={{ marginBottom: 8 }}>{statusNote}</Paragraph>
              <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
                书源依赖外部站点（番茄小说），随时可能因对方改版失效，
                因此与本地拆书能力隔离、默认关闭。关闭其不影响上传 TXT 的本地拆书导入。
              </Paragraph>
            </div>
          }
        />
      </div>
    );
  }

  if (enabled === null) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        {ctxHolder}
        <Spin size="large" />
      </div>
    );
  }

  // ============================================================
  //  正常界面
  // ============================================================
  return (
    <div className="source-page" style={sourceStyle}>
      {ctxHolder}

      <header className="source-header">
        <div>
          <Link to="/" className="source-back"><ArrowLeftOutlined /> 返回书架</Link>
          <div className="source-eyebrow"><TrophyOutlined /> 发现好故事</div>
          <Title level={2} className="source-title">番茄小说榜单</Title>
          <Text type="secondary">看看大家在读什么，点开一本了解更多。</Text>
        </div>
        <Space wrap>
          {/* 去番茄的入口。登录、选书都要在那边操作，
              放在这里省得用户自己去找地址。 */}
          <Button
            icon={<LinkOutlined />}
            href={FANQIE_HOME}
            target="_blank"
            rel="noopener noreferrer"
          >
            打开番茄小说
          </Button>
          <Button
            icon={<ReadOutlined />}
            href={FANQIE_RANK}
            target="_blank"
            rel="noopener noreferrer"
          >
            番茄排行榜
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadRank} loading={loadingRank}>
            刷新榜单
          </Button>
        </Space>
      </header>

      {/* ---------- 登录态 ----------
          这是最容易被误解的一处：未登录时平台对锁定章只给约 200 字符的
          试读片段，抓 550 章会「全部成功」但正文只覆盖很小一部分。
          所以把状态和入口摆在榜单之前。 */}
      {login && (
        <div className="source-notices">
          <Alert
            type={login.has_sessionid ? 'success' : 'warning'}
            showIcon
            icon={login.has_sessionid ? <InfoCircleOutlined /> : <WarningOutlined />}
            message={
              login.has_sessionid
                ? '已配置登录态，可取得锁定章全文'
                : login.configured
                  ? '已配置 Cookie，但缺少 sessionid —— 锁定章仍只能取到试读片段'
                  : '未配置登录态 —— 锁定章只能取到约 200 字的试读片段'
            }
            description={
              <div>
                <Paragraph style={{ fontSize: 12, marginBottom: 8 }}>
                  番茄对未登录访问只开放每本书前 10 章全文，第 11 章起一律
                  截断为约 200 字符的预览（页面提示「会员登录后，可在网页畅读全文」）。
                  要取整本，需要先登录番茄，再把登录态交给这里。
                </Paragraph>

                {/* 先去番茄登录，再回来取登录态。按顺序摆，避免用户跳错。 */}
                <Space wrap style={{ marginBottom: 10 }}>
                  <Button
                    size="small"
                    type={login.has_sessionid ? 'default' : 'primary'}
                    icon={<LinkOutlined />}
                    href={FANQIE_HOME}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    1. 去番茄登录
                  </Button>
                  {!login.has_sessionid && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      登录后回到本页，用下面的方式把登录态交给文枢
                    </Text>
                  )}
                </Space>

                <Space.Compact style={{ width: '100%', maxWidth: 720 }}>
                  <Input.Password
                    value={loginInput}
                    onChange={(e) => setLoginInput(e.target.value)}
                    placeholder="粘贴浏览器 Cookie（F12 → Network → 任意请求 → Request Headers → Cookie）"
                  />
                  <Button
                    type="primary"
                    loading={savingLogin}
                    onClick={() => void saveLogin()}
                  >
                    保存
                  </Button>
                  {login.configured && (
                    <Button
                      onClick={() => {
                        setLoginInput('');
                        void (async () => {
                          setSavingLogin(true);
                          try {
                            setLogin(await sourceApi.setLogin(''));
                            msgApi.success('登录态已清除');
                          } finally {
                            setSavingLogin(false);
                          }
                        })();
                      }}
                    >
                      清除
                    </Button>
                  )}
                </Space.Compact>
                <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
                  Cookie 只在本机保存并原样转发，不入库、不进 git。
                  {login.saved_to ? ` 位置：${login.saved_to}` : ''}
                </Paragraph>

                {/* 更省事的做法：让浏览器脚本自动带回登录态与书籍 */}
                {guide && (
                  <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px dashed rgba(0,0,0,.1)' }}>
                    <Text strong style={{ fontSize: 13 }}>
                      省事做法：装一次浏览器脚本，之后自动带回
                    </Text>
                    <Paragraph type="secondary" style={{ fontSize: 12, margin: '6px 0 10px' }}>
                      装好后，你在番茄网页上看到哪本书，点一下「交给文枢」——
                      书籍和登录态一起送过来，不用再手工粘 Cookie。
                    </Paragraph>
                    <Space wrap>
                      <Button type="primary" ghost size="small" onClick={installScript}>
                        安装浏览器脚本
                      </Button>
                      <Button size="small" onClick={() => void copyScriptUrl()}>
                        复制脚本地址
                      </Button>
                    </Space>
                    <div style={{ marginTop: 10 }}>
                      {guide.steps.map((s, i) => (
                        <div key={i} style={{ fontSize: 12, color: 'rgba(0,0,0,.55)', lineHeight: 1.9 }}>
                          {i + 1}. {s}
                        </div>
                      ))}
                    </div>
                    <Paragraph type="secondary" style={{ fontSize: 11, marginTop: 8, marginBottom: 0 }}>
                      {guide.notes.map((n, i) => (
                        <span key={i}>{n}{i < guide.notes.length - 1 ? ' ' : ''}</span>
                      ))}
                    </Paragraph>
                  </div>
                )}
              </div>
            }
          />
        </div>
      )}

      {/* ---------- 整本下载（外部下载器）----------
          与上面的取源任务分工不同：取源任务受平台对未登录访问的限制，
          锁定章只能拿到试读片段；整本下载交给外部下载器抓，含锁定章全文，
          抓完打包，再导出 TXT 交给拆书导入。 */}
      <Card
        size="small"
        title={<Space><Text strong>整本下载</Text>
          {extState && (
            <Tag color={EXT_STATE_META[extState]?.color}>
              {EXT_STATE_META[extState]?.label || extState}
            </Tag>
          )}
        </Space>}
        style={{ marginBottom: 16 }}
      >
        <Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
          由外部下载器抓取整本（含锁定章全文），下载完成后可导出 TXT 交给拆书导入。
        </Text>

        <Space.Compact style={{ width: '100%', maxWidth: 720, marginTop: 12 }}>
          <Input
            aria-label="书籍 ID 或链接"
            value={extInput}
            onChange={(e) => setExtInput(e.target.value)}
            onPressEnter={() => void startExtDownload()}
            placeholder="书籍 ID 或链接"
            allowClear
          />
          <Button
            type="primary"
            icon={<CloudDownloadOutlined />}
            loading={extStarting}
            onClick={() => void startExtDownload()}
          >
            下载整本
          </Button>
        </Space.Compact>

        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
          可以粘番茄的书籍链接，也可以直接填数字 ID；同一时间只跑一个任务，
          抓取由外部下载器完成，本页只显示进度。
        </Paragraph>

        {extError && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            message="整本下载失败"
            description={<Text type="secondary" style={{ fontSize: 12 }}>{extError}</Text>}
          />
        )}

        {extJobId !== null && (
          <div style={{ marginTop: 12 }}>
            {extStatus?.job?.progress ? (
              <>
                <Progress
                  percent={
                    extStatus.job.progress.chapter_total
                      ? Math.round(
                          (extStatus.job.progress.saved_chapters / extStatus.job.progress.chapter_total) * 100
                        )
                      : 0
                  }
                  status={
                    extState === 'error' ? 'exception'
                      : extState === 'done' ? 'success'
                      : 'active'
                  }
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {extStatus.job.progress.saved_chapters} / {extStatus.job.progress.chapter_total} 章
                  {' · '}分批 {extStatus.job.progress.group_done} / {extStatus.job.progress.group_total}
                  {extStatus.job.title && ` · ${extStatus.job.title}`}
                  {extStatus.job.author && `（${extStatus.job.author}）`}
                </Text>
              </>
            ) : (
              <Space>
                <Spin size="small" />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  任务 #{extJobId} 已提交，正在读取进度…
                </Text>
              </Space>
            )}

            {extState === 'done' && (
              <div style={{ marginTop: 10 }}>
                <Space wrap>
                  <Button
                    size="small"
                    type="primary"
                    icon={<SaveOutlined />}
                    onClick={downloadExtTxt}
                  >
                    下载 TXT
                  </Button>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    导出后可在拆书模块上传该 TXT 做导入
                    {extStatus?.epub_bytes
                      ? `（打包产物 ${(extStatus.epub_bytes / 1024 / 1024).toFixed(1)} MB）`
                      : ''}
                  </Text>
                </Space>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* ---------- 数据口径与覆盖范围 ----------
          这三条是必须保留的：不把部分当全部，并说明数据的时间口径。 */}
      {rank && (
        <div className="source-notices">
          <Alert
            type={rank.is_complete ? 'success' : 'warning'}
            showIcon
            icon={<InfoCircleOutlined />}
            message={rank.coverage_note}
            description={
              <Text type="secondary" style={{ fontSize: 12 }}>
                {rank.platform_note}
                {rank.enrich_failed > 0 && (
                  <> 其中 {rank.enrich_failed} 本详情补取失败，书名以榜单页为准，可能带字体混淆。</>
                )}
              </Text>
            }
          />
          {rank.garbled_titles > 0 && (
            <Alert
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              message={`有 ${rank.garbled_titles} 本书名可能未正确还原`}
              description={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  详情补取失败时只能退回榜单页书名，而榜单页书名带字体编码混淆。
                  点开详情可确认真实书名。
                </Text>
              }
            />
          )}
        </div>
      )}

      {/* ---------- 榜单 ---------- */}
      <section className="source-rank-panel" aria-label="小说榜单">
        <div className="source-rank-toolbar">
          <Segmented
            aria-label="作品状态"
            value={bookStatus}
            options={['全部', '连载', '完结']}
            onChange={setBookStatus}
          />
          <div className="source-search-controls">
            {categories.length > 1 && (
              <Select
                aria-label="筛选分类"
                value={category}
                onChange={setCategory}
                options={[{ value: '', label: '全部分类' }, ...categories.map((name) => ({ value: name, label: name }))]}
                style={{ minWidth: 116 }}
              />
            )}
            <Input
              className="source-search"
              aria-label="搜索榜单"
              placeholder="搜索榜单书名、作者"
              prefix={<SearchOutlined />}
              allowClear
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>
        {loadingRank && !rank ? (
          <div className="source-rank-skeleton" aria-label="正在打开榜单">
            {[0, 1, 2, 3, 4].map((index) => <Skeleton key={index} active avatar={{ shape: 'square', size: 64 }} paragraph={{ rows: 1 }} />)}
          </div>
        ) : visibleBooks.length ? (
          <ol className="source-rank-list">
            {visibleBooks.map(({ item, position }) => (
              <li key={item.book_id}>
                <button
                  type="button"
                  className="source-rank-row"
                  aria-label={`查看《${item.title}》详情`}
                  aria-haspopup="dialog"
                  onClick={() => openBook(item)}
                >
                  <span className={`source-rank-number${position <= 3 ? ` source-rank-top-${position}` : ''}`}>
                    {String(position).padStart(2, '0')}
                  </span>
                  <BookCover src={item.cover_url} title={item.title} />
                  <span className="source-rank-info">
                    <span className="source-rank-title">{item.title}</span>
                    <span className="source-rank-author">{item.author || '佚名'}</span>
                    <span className="source-rank-tags">
                      {item.category && <span>{item.category}</span>}
                      {item.status && <span>{item.status}</span>}
                      <span>{fmtCount(item.word_count)}字</span>
                    </span>
                  </span>
                  <span className="source-rank-readers">
                    <strong>{fmtCount(item.read_count)}</strong>
                    <span>人在读</span>
                  </span>
                  <RightOutlined className="source-rank-arrow" />
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <div className="source-rank-empty">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={rankError && !rank ? '暂时无法打开榜单' : query || category || bookStatus !== '全部' ? '没有找到符合条件的书籍' : '暂无榜单作品'}
            >
              {query || category || bookStatus !== '全部' ? (
                <Button onClick={() => { setQuery(''); setCategory(''); setBookStatus('全部'); }}>清除筛选</Button>
              ) : (
                <Button onClick={loadRank} loading={loadingRank}>重新打开</Button>
              )}
            </Empty>
          </div>
        )}
      </section>

      {/* ---------- 详情与选章 ---------- */}
      <Drawer
        title="书籍详情"
        rootClassName="source-book-drawer"
        width={isMobile ? '100%' : 720}
        open={!!selectedBook}
        onClose={closeBook}
        afterOpenChange={(open) => { if (!open) bookTrigger.current?.focus({ preventScroll: true }); }}
        styles={{ body: sourceStyle }}
        destroyOnHidden
      >
        {preview && (
          <>
            <div className="source-book-heading">
              <BookCover src={preview.cover_url} title={preview.title} large />
              <div className="source-book-heading-text">
                <Title level={3}>{preview.title}</Title>
                <Text type="secondary">{preview.author || '佚名'} 著</Text>
                <Space className="source-book-tags" size={[4, 8]} wrap>
                  {preview.status && <Tag color={preview.status.includes('完结') ? 'green' : 'cyan'}>{preview.status}</Tag>}
                  {[...new Set([preview.category, ...(preview.tags ?? [])].filter(Boolean))].map((tag) => <Tag key={tag}>{tag}</Tag>)}
                </Space>
                <Button
                  icon={<ReadOutlined />}
                  href={`https://fanqienovel.com/page/${encodeURIComponent(preview.book_id)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >在番茄阅读</Button>
              </div>
            </div>
            <div className="source-book-stats">
              <div><strong>{fmtCount(preview.read_count)}</strong><span>人在读</span></div>
              <div><strong>{fmtCount(preview.word_count)}</strong><span>字</span></div>
              {book && <div><strong>{book.chapters.length.toLocaleString()}</strong><span>章</span></div>}
            </div>
            <section className="source-book-intro" aria-label="作品简介">
              <Title level={5}>作品简介</Title>
              {!preview.abstract && loadingBook ? <Skeleton active title={false} paragraph={{ rows: 4 }} /> : (
                <Paragraph className="source-book-abstract">{preview.abstract?.trim() || '暂无简介'}</Paragraph>
              )}
            </section>
            {preview.last_chapter_title && (
              <div className="source-book-latest"><Text type="secondary">最新章节</Text><Text>{preview.last_chapter_title}</Text></div>
            )}
          </>
        )}
        {loadingBook && <div className="source-book-loading"><Spin size="small" /><Text type="secondary">正在加载目录…</Text></div>}
        {bookError && selectedBook && (
          <Alert
            type="error"
            showIcon
            message="暂时无法打开完整详情"
            action={<Button size="small" onClick={() => openBook(selectedBook)}>重试</Button>}
          />
        )}
        {book && (
          <Collapse
            className="source-chapter-picker"
            items={[{ key: book.book.book_id, label: <Space><BookOutlined />选择章节研读</Space>, children: (
            <div>
              <div style={{ marginTop: 8 }}>
                <Radio.Group
                  value={preset}
                  onChange={(e) => setPreset(e.target.value)}
                  optionType="button"
                  buttonStyle="solid"
                  size="small"
                >
                  <Radio.Button value="first">开篇若干章</Radio.Button>
                  <Radio.Button value="volume">指定卷</Radio.Button>
                  <Radio.Button value="all">整本</Radio.Button>
                </Radio.Group>
              </div>

              <div style={{ marginTop: 12 }}>
                {preset === 'first' && (
                  <Space>
                    <Text style={{ fontSize: 13 }}>取前</Text>
                    <InputNumber
                      size="small" min={1} max={200}
                      value={count} onChange={(v) => setCount(v ?? 12)}
                      style={{ width: 90 }} addonAfter="章"
                    />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      研究开篇时不用先请求整本
                    </Text>
                  </Space>
                )}

                {preset === 'volume' && (
                  <Space wrap>
                    <Text style={{ fontSize: 13 }}>选择卷</Text>
                    <Radio.Group
                      value={volume}
                      onChange={(e) => setVolume(e.target.value)}
                      size="small"
                    >
                      {book.volumes.map((v) => (
                        <Radio.Button key={v.name} value={v.name}>
                          {v.name}（{v.count}）
                        </Radio.Button>
                      ))}
                    </Radio.Group>
                  </Space>
                )}

                {preset === 'all' && (
                  <Alert
                    type="warning"
                    showIcon
                    message={`将抓取全部 ${book.chapters.length} 章`}
                    description={
                      <Text style={{ fontSize: 12 }}>
                        按当前间隔约需 {Math.ceil(book.chapters.length * interval / 60)} 分钟。
                        中途可取消；中断后可重新发起，已抓到的章不会重复请求。
                      </Text>
                    }
                  />
                )}
              </div>

              <div className="source-picker-actions">
                <Space size={8}>
                  <Text type="secondary">请求间隔</Text>
                  <InputNumber
                    aria-label="章节请求间隔"
                    size="small" min={0.2} max={10} step={0.2}
                    value={interval} onChange={(value) => setInterval(value ?? 1.2)}
                    style={{ width: 90 }} addonAfter="s"
                  />
                </Space>
                <Button
                  type="primary"
                  icon={<CloudDownloadOutlined />}
                  onClick={createTask}
                  disabled={preset === 'volume' && !volume}
                >
                  获取所选章节
                </Button>
              </div>
            </div>
            ) }]}
          />
        )}
      </Drawer>

      {/* ---------- 任务进度 ---------- */}
      {task && (
        <Card
          size="small"
          title={<Space><Text strong>取源任务</Text>
            <Tag color={TASK_STATUS_META[task.status]?.color}>
              {TASK_STATUS_META[task.status]?.label || task.status}
            </Tag>
          </Space>}
          extra={
            <Space>
              {!['completed', 'failed', 'cancelled'].includes(task.status) && (
                <Popconfirm title="取消这个任务？" onConfirm={cancelTask}>
                  <Button size="small" icon={<StopOutlined />} danger>取消</Button>
                </Popconfirm>
              )}
              <Button
                size="small" icon={<SaveOutlined />}
                disabled={task.ok_count === 0}
                onClick={downloadTxt}
              >
                导出 TXT
              </Button>
              <Button
                size="small" type="primary" icon={<ImportOutlined />}
                disabled={task.ok_count === 0}
                onClick={handoff}
              >
                交给拆书导入
              </Button>
            </Space>
          }
          style={{ marginBottom: 16 }}
        >
          <Row gutter={[16, 12]} align="middle">
            <Col xs={24} md={8}>
              <Progress
                percent={
                  task.total_chapters
                    ? Math.round((task.done_count / task.total_chapters) * 100)
                    : 0
                }
                status={
                  task.status === 'failed' ? 'exception'
                    : task.status === 'completed' ? 'success'
                    : 'active'
                }
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {task.done_count} / {task.total_chapters} 章
                {task.message && ` · ${task.message}`}
              </Text>
            </Col>
            <Col xs={12} md={5}>
              <Statistic title="成功" value={task.ok_count} valueStyle={{ fontSize: 18, color: token.colorSuccess }} />
            </Col>
            <Col xs={12} md={5}>
              <Statistic
                title="未成功" value={task.failed_count}
                valueStyle={{ fontSize: 18, color: task.failed_count ? token.colorWarning : undefined }}
              />
            </Col>
            <Col xs={24} md={6}>
              <Text type="secondary" style={{ fontSize: 11 }}>{task.book_title}</Text>
            </Col>
          </Row>

          {task.error && (
            <Alert type="error" showIcon style={{ marginTop: 12 }} message={task.error} />
          )}

          {task.status === 'paused' && (
            <Alert
              type="warning" showIcon style={{ marginTop: 12 }}
              message="任务已暂停"
              description="服务重启会中断抓取。已抓到的章节已保存，重新发起同范围任务时会跳过它们。"
            />
          )}

          {/* ---------- 拆书模块的反应 ----------
              抓完自动交给拆书后，这里显示对方的状态。
              没有这一块的话，从书源侧看不到交接之后发生了什么，
              用户只能凭「已完成」猜后续跑没跑。 */}
          {task.import_status === 'handed' && task.import_task_id && (
            <Alert
              type="success"
              showIcon
              icon={<ImportOutlined />}
              style={{ marginTop: 12 }}
              message={
                <Space wrap>
                  <span>已交给拆书模块</span>
                  {importTask && (
                    <Tag color={
                      importTask.status === 'completed' ? 'green'
                        : importTask.status === 'failed' ? 'red'
                        : 'blue'
                    }>
                      {BOOK_IMPORT_STATUS_LABEL[importTask.status] || importTask.status}
                    </Tag>
                  )}
                </Space>
              }
              description={
                <div>
                  <Paragraph style={{ fontSize: 12, marginBottom: 8 }}>
                    {task.import_message}
                  </Paragraph>
                  {importTask && (
                    <>
                      <Progress
                        percent={importTask.progress ?? 0}
                        size="small"
                        status={
                          importTask.status === 'failed' ? 'exception'
                            : importTask.status === 'completed' ? 'success'
                            : 'active'
                        }
                      />
                      {importTask.message && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {importTask.message}
                        </Text>
                      )}
                      {importTask.ai_degraded && (
                        <div style={{ marginTop: 6 }}>
                          <Tag color="orange" icon={<WarningOutlined />}>
                            未使用 AI
                          </Tag>
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            产出为规则推断
                            {importTask.ai_fallback_reasons?.length
                              ? `（${importTask.ai_fallback_reasons.join('、')}）`
                              : ''}
                          </Text>
                        </div>
                      )}
                    </>
                  )}
                  <div style={{ marginTop: 10 }}>
                    <Space wrap>
                      <Button
                        size="small"
                        icon={<RightOutlined />}
                        onClick={() => navigate('/?view=book-import')}
                      >
                        去拆书模块确认导入
                      </Button>
                      <Button
                        size="small"
                        icon={<ImportOutlined />}
                        onClick={() => void handoff()}
                      >
                        重新交接
                      </Button>
                    </Space>
                  </div>
                </div>
              }
            />
          )}

          {task.import_status === 'failed' && (
            <Alert
              type="warning"
              showIcon
              icon={<WarningOutlined />}
              style={{ marginTop: 12 }}
              message="交给拆书模块失败"
              description={
                <div>
                  <Paragraph style={{ fontSize: 12, marginBottom: 8 }}>
                    {task.import_message || '未知原因'}
                  </Paragraph>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    抓取本身是成功的，正文已保存。可以重试交接。
                  </Text>
                  <div style={{ marginTop: 8 }}>
                    <Button size="small" onClick={() => void handoff()}>
                      重试
                    </Button>
                  </div>
                </div>
              }
            />
          )}
        </Card>
      )}

      {/* ---------- 分章结果 ---------- */}
      {chapters && (
        <Card
          size="small"
          title={<Space><Text strong>分章结果</Text>
            <Tag color="green">{chapters.report.ok} 成功</Tag>
            {chapters.report.missing_count > 0 && (
              <Tag color="orange">{chapters.report.missing_count} 未成功</Tag>
            )}
          </Space>}
          style={{ marginBottom: 16 }}
        >
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
            {chapters.scope_note}
          </Text>

          {/* 缺章清单：用户据此判断分析覆盖了多少。
              preview（仅试读片段）也计入这里 —— 它看起来有正文，
              实际是被服务端截断的片段，不能算取得。 */}
          {chapters.report.missing_count > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message={
                chapters.report.preview_count
                  ? `有 ${chapters.report.missing_count} 章不是完整正文` +
                    `（其中 ${chapters.report.preview_count} 章只取得试读片段）`
                  : `有 ${chapters.report.missing_count} 章未取得正文`
              }
              description={
                <div style={{ maxHeight: 140, overflowY: 'auto' }}>
                  {chapters.report.missing.slice(0, 40).map((m) => (
                    <div key={m.item_id} style={{ fontSize: 12 }}>
                      <Text>第{m.order}章 {m.title}</Text>
                      <Tag
                        style={{ marginLeft: 6 }}
                        color={STATUS_META[m.status]?.color}
                      >
                        {STATUS_META[m.status]?.label || m.status}
                      </Tag>
                      {m.status === 'preview' && m.claimed_words ? (
                        <Text type="secondary">
                          {' '}取得 {m.chars} 字 / 申报 {m.claimed_words} 字
                        </Text>
                      ) : null}
                      {m.error && <Text type="secondary"> {m.error.slice(0, 50)}</Text>}
                    </div>
                  ))}
                  {chapters.report.missing.length > 40 && (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      …另有 {chapters.report.missing.length - 40} 章未列出
                    </Text>
                  )}
                </div>
              }
            />
          )}

          <Table<SourceChapterResult>
            size="small"
            rowKey="item_id"
            pagination={{ pageSize: 20, size: 'small', showSizeChanger: false }}
            scroll={{ x: 'max-content' }}
            dataSource={chapters.chapters}
            columns={[
              { title: '章', dataIndex: 'order', width: 56, align: 'center' },
              { title: '标题', dataIndex: 'title', ellipsis: true },
              {
                title: '卷', dataIndex: 'volume', width: 130, ellipsis: true,
                render: (v: string) => <Text type="secondary" style={{ fontSize: 12 }}>{v || '—'}</Text>,
              },
              {
                title: '状态', dataIndex: 'status', width: 96,
                render: (s: string) => (
                  <Tag color={STATUS_META[s]?.color}>{STATUS_META[s]?.label || s}</Tag>
                ),
              },
              {
                title: '字数', dataIndex: 'chars', width: 80, align: 'right',
                render: (v: number) => v || '—',
              },
              {
                title: '还原', width: 110, align: 'center',
                render: (_v, r) => {
                  if (!r.pua_before) return <Text type="secondary">—</Text>;
                  return (
                    <Tooltip title={`原乱码 ${r.pua_before} 字 → 残留 ${r.pua_after} 字`}>
                      <Text style={{ fontSize: 12 }}>
                        {r.pua_before} → {r.pua_after}
                      </Text>
                      {r.pua_after === 0
                        ? <Badge status="success" style={{ marginLeft: 6 }} />
                        : <Badge status="error" style={{ marginLeft: 6 }} />}
                    </Tooltip>
                  );
                },
              },
            ]}
          />
        </Card>
      )}

      {/* ---------- 历史任务 ---------- */}
      {tasks.length > 0 && (
        <Card size="small" title={<Text strong>最近任务</Text>}>
          <List
            size="small"
            dataSource={tasks}
            renderItem={(t) => (
              <List.Item
                actions={[
                  <Button
                    key="open" size="small" type="link"
                    onClick={async () => {
                      const full = await sourceApi.task(t.task_id);
                      setTask(full);
                      const ch = await sourceApi.taskChapters(t.task_id);
                      setChapters(ch);
                    }}
                  >
                    查看
                  </Button>,
                ]}
              >
                <Space>
                  <Tag color={TASK_STATUS_META[t.status]?.color}>
                    {TASK_STATUS_META[t.status]?.label || t.status}
                  </Tag>
                  <Text style={{ fontSize: 13 }}>{t.book_title || t.book_id}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t.ok_count}/{t.total_chapters} 章
                  </Text>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}
    </div>
  );
}
