import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
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
} from '@ant-design/icons';
import { sourceApi } from '../services/api';
import type {
  SourceBookResponse,
  SourceChapterResult,
  SourceLoginState,
  SourceRankItem,
  SourceRankResponse,
  SourceSelection,
  SourceTask,
  SourceTaskChaptersResponse,
} from '../types';
import './Source.css';

const { Text, Title, Paragraph } = Typography;

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
          <div className="source-eyebrow"><TrophyOutlined /> 发现好故事</div>
          <Title level={2} className="source-title">番茄小说榜单</Title>
          <Text type="secondary">看看大家在读什么，点开一本了解更多。</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={loadRank} loading={loadingRank}>刷新榜单</Button>
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
                  要取整本，需要在下方填入浏览器登录后的 Cookie。
                </Paragraph>
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
              </div>
            }
          />
        </div>
      )}

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
