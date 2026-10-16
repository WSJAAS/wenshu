import { useState, useEffect, useCallback } from 'react';
import {
  Card,
  Button,
  Input,
  Space,
  Tag,
  Typography,
  Row,
  Col,
  Switch,
  message,
  Modal,
  Statistic,
  Divider,
  Alert,
  Tooltip,
  Radio,
  Empty,
  Spin,
} from 'antd';
import {
  ThunderboltOutlined,
  ExperimentOutlined,
  SaveOutlined,
  RollbackOutlined,
  SwapOutlined,
  CheckCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { polishApi } from '../services/api';
import type { PolishResponse, AcceptRecord } from '../types';

const { TextArea } = Input;
const { Text, Paragraph, Title } = Typography;

/** 把任意诊断对象转成可读的键值列表 */
function toRows(obj: Record<string, unknown> | null | undefined): Array<[string, string]> {
  if (!obj) return [];
  const rows: Array<[string, string]> = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === null || v === undefined) continue;
    let display: string;
    if (typeof v === 'number') {
      display = Number.isInteger(v) ? String(v) : v.toFixed(2);
    } else if (typeof v === 'boolean') {
      display = v ? '是' : '否';
    } else if (Array.isArray(v)) {
      display = v.length ? v.slice(0, 6).join('、') : '—';
    } else if (typeof v === 'object') {
      continue; // 嵌套对象不铺开，避免噪声
    } else {
      display = String(v);
    }
    rows.push([k, display]);
  }
  return rows;
}

export default function Polish() {
  const [text, setText] = useState('');
  const [result, setResult] = useState<PolishResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [useModel, setUseModel] = useState(true);
  const [mode, setMode] = useState<'novel' | 'paper'>('novel');
  const [accepted, setAccepted] = useState<AcceptRecord | null>(null);
  const [versions, setVersions] = useState<AcceptRecord[]>([]);
  const [engineOk, setEngineOk] = useState<boolean | null>(null);
  const [msgApi, ctxHolder] = message.useMessage();
  const [modal, modalHolder] = Modal.useModal();

  // 引擎状态 + 采纳历史
  const refreshSide = useCallback(async () => {
    try {
      const e = await polishApi.engine();
      setEngineOk(e.available);
    } catch {
      setEngineOk(false);
    }
    try {
      const v = await polishApi.versions(mode);
      setVersions(v.items || []);
    } catch {
      setVersions([]);
    }
  }, [mode]);

  useEffect(() => {
    refreshSide();
  }, [refreshSide]);

  // ---- 改写 ----
  const handlePolish = async () => {
    const input = text.trim();
    if (!input) {
      msgApi.warning('请先粘贴或输入原稿');
      return;
    }
    setRunning(true);
    try {
      const r = await polishApi.polishText({
        original_text: input,
        mode,
        use_model: useModel,
      });
      setResult(r);
      msgApi.success(
        r.engine === 'model'
          ? '模型改写完成，已生成候选稿（原稿未改动）'
          : '本地规则改写完成，已生成候选稿（原稿未改动）',
      );
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`改写失败：${err?.response?.data?.detail || err?.message || '未知错误'}`);
    } finally {
      setRunning(false);
    }
  };

  // ---- 采纳 ----
  const handleAccept = async () => {
    if (!result?.polished_text) {
      msgApi.warning('没有可采纳的候选稿');
      return;
    }
    try {
      const r = await polishApi.accept(result.polished_text, mode);
      setAccepted(r.saved);
      msgApi.success(`已采纳为第 ${r.versions} 版`);
      refreshSide();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`采纳失败：${err?.response?.data?.detail || err?.message || '未知错误'}`);
    }
  };

  // ---- 采纳（二次确认） ----
  const confirmAccept = () => {
    if (!result?.polished_text) {
      msgApi.warning('没有可采纳的候选稿');
      return;
    }
    const isLocalRules = result.engine === 'local_rules';
    modal.confirm({
      title: '确认采纳候选稿',
      content: isLocalRules
        ? '当前候选稿来自本地规则改写（不是模型生成），质量可能有限。采纳后将作为新版本落盘，原稿不受影响。'
        : '采纳后将作为新版本落盘，原稿不受影响，可通过「回退」取回上一版。',
      centered: true,
      okText: '确认采纳',
      cancelText: '取消',
      ...(isLocalRules ? { okButtonProps: { type: 'default' as const } } : {}),
      onOk: handleAccept,
    });
  };

  // ---- 回退 ----
  const handleRollback = async () => {
    try {
      const r = await polishApi.rollback(mode);
      setText(r.text);
      setResult(null);
      msgApi.success('已回退到上一版，内容已填回输入框');
      refreshSide();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`回退失败：${err?.response?.data?.detail || err?.message || '未知错误'}`);
    }
  };

  const beforeRows = toRows(result?.diagnostics_before as Record<string, unknown>);
  const afterRows = toRows(result?.diagnostics_after as Record<string, unknown>);
  const metricKeys = Array.from(new Set([...beforeRows.map((r) => r[0]), ...afterRows.map((r) => r[0])]));

  const isMobile = window.innerWidth <= 768;

  return (
    <div style={{ padding: isMobile ? 12 : 24, maxWidth: 1600, margin: '0 auto' }}>
      {ctxHolder}
      {modalHolder}

      <div style={{ marginBottom: 16 }}>
        <Title level={4} style={{ marginBottom: 4 }}>
          <ThunderboltOutlined /> 文本重塑
        </Title>
        <Text type="secondary">
          原稿与候选稿严格分离：改写只产出候选稿，采纳后才写入，且随时可回退。
        </Text>
      </div>

      {engineOk === false && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="文本重塑引擎不可用"
          description="未找到 engine/ 目录或其模块导入失败。本地规则改写与文本体检将无法使用。"
        />
      )}

      {/* ---------- 控制区 ---------- */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap size="middle" align="center">
          <Radio.Group
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            optionType="button"
            buttonStyle="solid"
            options={[
              { label: '文学小说', value: 'novel' },
              { label: '学术论文', value: 'paper' },
            ]}
          />
          <Tooltip title="关闭后使用本地规则表改写，不调用模型，离线可用">
            <Space size={6}>
              <Switch checked={useModel} onChange={setUseModel} size="small" />
              <Text style={{ fontSize: 13 }}>调用模型</Text>
            </Space>
          </Tooltip>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={running}
            onClick={handlePolish}
          >
            生成候选稿
          </Button>
          <Button icon={<ReloadOutlined />} onClick={refreshSide} size="small">
            刷新
          </Button>
        </Space>
      </Card>

      {/* ---------- 左右对照 ---------- */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card
            size="small"
            title={<Space><Text strong>原稿</Text><Tag>不会被修改</Tag></Space>}
            styles={{ body: { padding: 12 } }}
          >
            <TextArea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="粘贴需要处理的文本…"
              autoSize={{ minRows: 14, maxRows: 26 }}
              style={{ fontFamily: 'inherit', fontSize: 14, lineHeight: 1.9 }}
            />
            <div style={{ marginTop: 8, textAlign: 'right' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {text.length} 字符
              </Text>
            </div>
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card
            size="small"
            title={
              <Space>
                <Text strong>候选稿</Text>
                {result?.engine && (
                  <Tag color={result.engine === 'model' ? 'green' : 'orange'}>
                    {result.engine === 'model' ? '模型改写' : '本地规则'}
                  </Tag>
                )}
              </Space>
            }
            extra={
              <Space>
                <Button
                  size="small"
                  type="primary"
                  icon={<SaveOutlined />}
                  disabled={!result?.polished_text}
                  onClick={confirmAccept}
                >
                  采纳
                </Button>
                <Button
                  size="small"
                  icon={<RollbackOutlined />}
                  disabled={versions.length < 2}
                  onClick={handleRollback}
                >
                  回退
                </Button>
              </Space>
            }
            styles={{ body: { padding: 12 } }}
          >
            {running ? (
              <div style={{ textAlign: 'center', padding: '60px 0' }}>
                <Spin tip="处理中…" />
              </div>
            ) : result?.polished_text ? (
              <>
                <Paragraph
                  style={{
                    whiteSpace: 'pre-wrap',
                    fontSize: 14,
                    lineHeight: 1.9,
                    marginBottom: 8,
                    maxHeight: 460,
                    overflowY: 'auto',
                  }}
                >
                  {result.polished_text}
                </Paragraph>
                <div style={{ textAlign: 'right' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {result.word_count_before} → {result.word_count_after} 字符
                  </Text>
                </div>
              </>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={<Text type="secondary">候选稿会显示在这里</Text>}
                style={{ padding: '40px 0' }}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* ---------- 引擎提示 ---------- */}
      {result?.engine_note && (
        <Alert
          type={result.engine === 'local_rules' ? 'warning' : 'info'}
          showIcon
          style={{ marginTop: 16 }}
          message={result.engine_note}
        />
      )}

      {/* ---------- 指标对照 ---------- */}
      {result && metricKeys.length > 0 && (
        <Card
          size="small"
          style={{ marginTop: 16 }}
          title={<Space><ExperimentOutlined /><Text strong>文本体检</Text></Space>}
        >
          <Row gutter={[16, 8]}>
            <Col xs={24} md={12}>
              <Text type="secondary" style={{ fontSize: 12 }}>改写前</Text>
              {beforeRows.length ? (
                <Row gutter={[8, 8]} style={{ marginTop: 8 }}>
                  {beforeRows.map(([k, v]) => (
                    <Col span={8} key={k}>
                      <Statistic title={<span style={{ fontSize: 11 }}>{k}</span>} value={v} valueStyle={{ fontSize: 15 }} />
                    </Col>
                  ))}
                </Row>
              ) : (
                <div><Text type="secondary">无数据</Text></div>
              )}
            </Col>
            <Col xs={24} md={12}>
              <Text type="secondary" style={{ fontSize: 12 }}>改写后</Text>
              {afterRows.length ? (
                <Row gutter={[8, 8]} style={{ marginTop: 8 }}>
                  {afterRows.map(([k, v]) => (
                    <Col span={8} key={k}>
                      <Statistic title={<span style={{ fontSize: 11 }}>{k}</span>} value={v} valueStyle={{ fontSize: 15 }} />
                    </Col>
                  ))}
                </Row>
              ) : (
                <div><Text type="secondary">无数据</Text></div>
              )}
            </Col>
          </Row>

          {result.applied_rules && result.applied_rules.length > 0 && (
            <>
              <Divider style={{ margin: '12px 0' }} />
              <Text type="secondary" style={{ fontSize: 12 }}>
                命中规则 {result.applied_rules.length} 条
              </Text>
              <div style={{ marginTop: 6 }}>
                <Space wrap size={[4, 4]}>
                  {result.applied_rules.slice(0, 20).map((r, i) => (
                    <Tag key={i} style={{ fontSize: 11 }}>{r}</Tag>
                  ))}
                </Space>
              </div>
            </>
          )}
        </Card>
      )}

      {/* ---------- 版本 ---------- */}
      <Card
        size="small"
        style={{ marginTop: 16 }}
        title={<Space><SwapOutlined /><Text strong>采纳版本</Text><Tag>{versions.length}</Tag></Space>}
      >
        {versions.length === 0 ? (
          <Text type="secondary">还没有采纳记录。生成候选稿后点「采纳」即可留档。</Text>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }} size={4}>
            {versions.slice().reverse().slice(0, 8).map((v) => (
              <div key={v.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                <Space>
                  <CheckCircleOutlined style={{ color: v.name === accepted?.name ? '#52c41a' : undefined }} />
                  <Text>{v.name}</Text>
                </Space>
                <Text type="secondary">{v.chars} 字符 · {v.saved_at}</Text>
              </div>
            ))}
          </Space>
        )}
      </Card>
    </div>
  );
}
