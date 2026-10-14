import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  InputNumber,
  List,
  message,
  Popconfirm,
  Progress,
  Radio,
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
  ReloadOutlined,
  SaveOutlined,
  StopOutlined,
  TrophyOutlined,
  ImportOutlined,
  WarningOutlined,
  InfoCircleOutlined,
  FileTextOutlined,
} from '@ant-design/icons';
import { sourceApi } from '../services/api';
import type {
  SourceBookResponse,
  SourceChapterResult,
  SourceRankItem,
  SourceRankResponse,
  SourceSelection,
  SourceTask,
  SourceTaskChaptersResponse,
} from '../types';

const { Text, Title, Paragraph } = Typography;

/** 逐章状态的展示样式 */
const STATUS_META: Record<string, { color: string; label: string }> = {
  ok: { color: 'success', label: '成功' },
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

export default function Source() {
  const { token } = theme.useToken();
  const [msgApi, ctxHolder] = message.useMessage();

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [statusNote, setStatusNote] = useState('');

  const [rank, setRank] = useState<SourceRankResponse | null>(null);
  const [loadingRank, setLoadingRank] = useState(false);

  const [book, setBook] = useState<SourceBookResponse | null>(null);
  const [loadingBook, setLoadingBook] = useState(false);

  const [preset, setPreset] = useState<'first' | 'volume' | 'all'>('first');
  const [count, setCount] = useState(12);
  const [volume, setVolume] = useState('');
  const [interval, setInterval] = useState(1.2);

  const [task, setTask] = useState<SourceTask | null>(null);
  const [chapters, setChapters] = useState<SourceTaskChaptersResponse | null>(null);
  const [tasks, setTasks] = useState<SourceTask[]>([]);

  // ---- 模块状态 ----
  useEffect(() => {
    sourceApi.status()
      .then((s) => {
        setEnabled(s.enabled);
        setStatusNote(s.note);
      })
      .catch(() => setEnabled(false));
  }, []);

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
    setLoadingRank(true);
    try {
      const r = await sourceApi.rank(true, interval);
      setRank(r);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`读取榜单失败：${err?.response?.data?.detail || err?.message}`);
    } finally {
      setLoadingRank(false);
    }
  }, [interval, msgApi]);

  useEffect(() => {
    if (enabled) loadRank();
  }, [enabled, loadRank]);

  // ---- 详情 ----
  const openBook = async (bookId: string) => {
    setLoadingBook(true);
    setBook(null);
    setTask(null);
    setChapters(null);
    try {
      const b = await sourceApi.book(bookId, interval);
      setBook(b);
      if (b.volumes.length) setVolume(b.volumes[0].name);
      // 滚动到选章区
      setTimeout(() => {
        document.getElementById('source-picker')?.scrollIntoView({
          behavior: 'smooth', block: 'start',
        });
      }, 100);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`读取详情失败：${err?.response?.data?.detail || err?.message}`);
    } finally {
      setLoadingBook(false);
    }
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

  const isMobile = window.innerWidth <= 768;

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
    <div style={{ padding: isMobile ? 12 : 24, maxWidth: 1440, margin: '0 auto' }}>
      {ctxHolder}

      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <Title level={4} style={{ marginBottom: 4 }}>
            <CloudDownloadOutlined /> 在线书源
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            从公开页面取书 → 还原字体编码 → 交本地拆书分析。内容仅作本地研读。
          </Text>
        </div>
        <Space>
          <Tooltip title="请求间隔。实测单章 0.2~0.4s，但连续整本的风控代价未知，建议保持 1s 以上">
            <Space size={4}>
              <Text type="secondary" style={{ fontSize: 12 }}>间隔</Text>
              <InputNumber
                size="small" min={0.2} max={10} step={0.2}
                value={interval} onChange={(v) => setInterval(v ?? 1.2)}
                style={{ width: 72 }} addonAfter="s"
              />
            </Space>
          </Tooltip>
          <Button size="small" icon={<ReloadOutlined />} onClick={loadRank} loading={loadingRank}>
            刷新榜单
          </Button>
        </Space>
      </div>

      {/* ---------- 榜单 ---------- */}
      <Card
        size="small"
        title={<Space><TrophyOutlined /><Text strong>榜单</Text>
          {rank && <Tag>{rank.fetched} 本</Tag>}
        </Space>}
        extra={
          <Tooltip title={rank?.platform_note}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              <InfoCircleOutlined /> 数据口径
            </Text>
          </Tooltip>
        }
        style={{ marginBottom: 16 }}
        loading={loadingRank && !rank}
      >
        {rank && (
          <>
            {/* 覆盖范围：必须如实展示，不能假装是完整榜单 */}
            <Alert
              type={rank.is_complete ? 'success' : 'warning'}
              showIcon
              style={{ marginBottom: 12 }}
              message={rank.coverage_note}
              description={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {rank.platform_note}
                  {rank.enrich_failed > 0 && (
                    <> 详情补取失败 {rank.enrich_failed} 本，书名以榜单页为准。</>
                  )}
                </Text>
              }
            />

            <Table<SourceRankItem>
              size="small"
              rowKey="book_id"
              pagination={false}
              scroll={{ x: 'max-content' }}
              dataSource={rank.items}
              columns={[
                {
                  title: '#', width: 44, align: 'center',
                  render: (_v, _r, i) => i + 1,
                },
                {
                  title: '书名', dataIndex: 'title', ellipsis: true,
                  render: (t: string, r) => (
                    <Space size={4}>
                      <Text strong style={{ fontSize: 13 }}>{t}</Text>
                      {r.title_may_be_garbled && (
                        <Tooltip title="详情补取失败，书名取自榜单页，可能带字体混淆">
                          <WarningOutlined style={{ color: token.colorWarning }} />
                        </Tooltip>
                      )}
                    </Space>
                  ),
                },
                { title: '作者', dataIndex: 'author', width: 130, ellipsis: true },
                {
                  title: '在读', dataIndex: 'read_count', width: 100, align: 'right',
                  render: (v: number) => fmtCount(v),
                },
                {
                  title: '字数', dataIndex: 'word_count', width: 100, align: 'right',
                  render: (v: number) => fmtCount(v),
                },
                {
                  title: '分类', dataIndex: 'category', width: 110, ellipsis: true,
                  render: (c: string) => c ? <Tag color="blue">{c}</Tag> : <Text type="secondary">—</Text>,
                },
                {
                  title: '状态', dataIndex: 'status', width: 70,
                  render: (s: string) => s
                    ? <Tag color={s === '完结' ? 'green' : 'orange'}>{s}</Tag>
                    : <Text type="secondary">—</Text>,
                },
                {
                  title: '操作', width: 90, fixed: 'right',
                  render: (_v, r) => (
                    <Button
                      size="small" type="link"
                      onClick={() => openBook(r.book_id)}
                    >
                      选章
                    </Button>
                  ),
                },
              ]}
            />
          </>
        )}
      </Card>

      {/* ---------- 详情与选章 ---------- */}
      {book && (
        <Card
          id="source-picker"
          size="small"
          title={<Space><FileTextOutlined /><Text strong>{book.book.title}</Text></Space>}
          extra={<Button size="small" onClick={() => setBook(null)}>收起</Button>}
          style={{ marginBottom: 16 }}
          loading={loadingBook}
        >
          <Row gutter={[16, 12]}>
            <Col xs={24} lg={10}>
              <Descriptions size="small" column={1} bordered>
                <Descriptions.Item label="作者">{book.book.author || '—'}</Descriptions.Item>
                <Descriptions.Item label="分类">
                  <Space size={4} wrap>
                    {book.book.category && <Tag color="blue">{book.book.category}</Tag>}
                    {book.book.tags.filter((t) => t !== book.book.category).map((t) => (
                      <Tag key={t}>{t}</Tag>
                    ))}
                  </Space>
                </Descriptions.Item>
                <Descriptions.Item label="体量">
                  {fmtCount(book.book.word_count)} 字 · {book.chapters.length} 章
                </Descriptions.Item>
                <Descriptions.Item label="热度">
                  {fmtCount(book.book.read_count)} 人在读
                </Descriptions.Item>
                <Descriptions.Item label="状态">{book.book.status || '—'}</Descriptions.Item>
                <Descriptions.Item label="最新章节">
                  <Text style={{ fontSize: 12 }}>{book.book.last_chapter_title || '—'}</Text>
                </Descriptions.Item>
              </Descriptions>

              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message="关于锁定标记"
                description={
                  <Text style={{ fontSize: 12 }}>{book.lock_field_note}</Text>
                }
              />
            </Col>

            <Col xs={24} lg={14}>
              <Text strong style={{ fontSize: 13 }}>选择要抓取的章节</Text>
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

              <div style={{ marginTop: 16 }}>
                <Button
                  type="primary"
                  icon={<CloudDownloadOutlined />}
                  onClick={createTask}
                  disabled={preset === 'volume' && !volume}
                >
                  开始抓取
                </Button>
              </div>
            </Col>
          </Row>
        </Card>
      )}

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

          {/* 缺章清单：用户据此判断分析覆盖了多少 */}
          {chapters.report.missing_count > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message={`有 ${chapters.report.missing_count} 章未取得正文`}
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
