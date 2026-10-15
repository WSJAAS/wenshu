import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Grid,
  Input,
  List,
  message,
  Popconfirm,
  Progress,
  Row,
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
  SaveOutlined,
  StopOutlined,
  ImportOutlined,
  ArrowLeftOutlined,
  BookOutlined,
  InfoCircleOutlined,
  WarningOutlined,
  LinkOutlined,
} from '@ant-design/icons';
import { sourceApi, bookImportApi, extDownloadApi } from '../services/api';
import type {
  BookImportTask,
  ExtDownloadJob,
  ExtDownloadStatus,
  SourceChapterResult,
  SourceLoginState,
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

export default function Source() {
  const { token } = theme.useToken();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const navigate = useNavigate();
  const [msgApi, ctxHolder] = message.useMessage();

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [statusNote, setStatusNote] = useState('');

  // 登录态。只影响旧版逐章抓取通道的免费章区间，
  // 整本全文走外部下载器（App 接口）。保留展示避免误导。
  const [login, setLogin] = useState<SourceLoginState | null>(null);

  const [task, setTask] = useState<SourceTask | null>(null);
  const [chapters, setChapters] = useState<SourceTaskChaptersResponse | null>(null);
  const [tasks, setTasks] = useState<SourceTask[]>([]);

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
          <div className="source-eyebrow"><BookOutlined /> 发现好故事</div>
          <Title level={2} className="source-title">在线书源 · 整本下载</Title>
          <Text type="secondary">粘贴书籍链接或 ID，整本取回全文（含锁定章）。</Text>
        </div>
        <Space wrap>
          {/* 去番茄的入口。找书、复制链接都要在那边操作，
              放在这里省得用户自己去找地址。 */}
          <Button
            icon={<LinkOutlined />}
            href={FANQIE_HOME}
            target="_blank"
            rel="noopener noreferrer"
          >
            打开番茄小说
          </Button>
        </Space>
      </header>

      {/* ---------- 登录态 ----------
          登录态 Cookie 只影响「免费章区间」的逐章抓取通道。
          整本全文走下面的外部下载器（App 接口），不受此限制；
          普通登录拿不到网页畅读（那要 SVIP），所以这里不再宣称
          「配置登录态即可取锁定章全文」。 */}
      {login && (
        <div className="source-notices">
          <Alert
            type={login.has_sessionid ? 'info' : 'warning'}
            showIcon
            icon={login.has_sessionid ? <InfoCircleOutlined /> : <WarningOutlined />}
            message={
              login.has_sessionid
                ? '已配置登录态（用于免费章抓取）'
                : login.configured
                  ? '已配置 Cookie，但缺少 sessionid'
                  : '未配置登录态'
            }
            description={
              <div>
                <Paragraph style={{ fontSize: 12, marginBottom: 8 }}>
                  说明：番茄只对 APP 端免费开放全书，网页端第 11 章起一律
                  截断为约 200 字符（实测普通登录也一样，网页畅读需 SVIP）。
                  <Text strong>整本全文不受影响</Text>
                  —— 用下面的「整本下载」，它走 App 接口，锁定章也是全文。
                  登录态只影响旧版逐章抓取通道的免费章区间。
                </Paragraph>
              </div>
            }
          />
        </div>
      )}

      {/* ---------- 整本下载（外部下载器）----------
          本页的主入口。走 App 接口整本抓取，锁定章也是全文，
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
          走 App 接口整本抓取，锁定章也是全文（实测 550 章 / 120 万字 / 约 1 分钟）。
          下载完成后可导出 TXT 交给拆书导入。
        </Text>

        <Space.Compact style={{ width: '100%', maxWidth: 720, marginTop: 12 }}>
          <Input
            aria-label="书籍 ID 或链接"
            value={extInput}
            onChange={(e) => setExtInput(e.target.value)}
            onPressEnter={() => void startExtDownload()}
            placeholder="书籍 ID 或链接（如 https://fanqienovel.com/page/7231424472606051384）"
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
          在番茄网页上打开要下的书，把地址栏链接粘到这里即可；也可以直接填数字 ID。
          同一时间只跑一个任务，抓取由外部下载器完成，本页只显示进度。
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
                        icon={<ArrowLeftOutlined rotate={-90} />}
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
