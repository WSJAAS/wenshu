import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Input,
  message,
  Modal,
  Select,
  Space,
  Spin,
  Typography,
} from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  KeyOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { settingsApi } from '../services/api';
import type { ApiConfigStatus } from '../types';

const { Text, Paragraph } = Typography;

/** 常见服务商预设。base_url 留空表示用该服务商的官方地址。 */
const PROVIDERS = [
  { value: 'openai', label: 'OpenAI', base: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { value: 'deepseek', label: 'DeepSeek', base: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { value: 'moonshot', label: '月之暗面 Kimi', base: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { value: 'zhipu', label: '智谱 GLM', base: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  { value: 'dashscope', label: '阿里通义', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { value: 'siliconflow', label: '硅基流动', base: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-7B-Instruct' },
  { value: 'openrouter', label: 'OpenRouter', base: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini' },
  { value: 'custom', label: '自定义（兼容 OpenAI 接口）', base: '', model: '' },
];

/**
 * AI 配置引导。
 *
 * 进入应用时若检测到未配置 API Key，弹出一次。填好后不再打扰。
 *
 * 设计上刻意做成「可跳过」：工具本身的拆书、书源、
 * 文本重塑的本地规则都不依赖 AI，没有理由拦住用户。
 * 但要让用户知道 —— 不配 Key 的话，生成类功能会回退到规则推断。
 */
export default function ApiConfigGate() {
  const [msgApi, ctxHolder] = message.useMessage();
  const [open, setOpen] = useState(false);
  const [checking, setChecking] = useState(true);
  const [status, setStatus] = useState<ApiConfigStatus | null>(null);

  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const [form] = Form.useForm();

  // 是否已经提示过（同一个浏览器会话内不再重复弹）
  const DISMISS_KEY = 'wenshu_api_gate_dismissed_v1';

  const load = useCallback(async () => {
    try {
      const s = await settingsApi.getApiConfigStatus();
      setStatus(s);

      // 已配置 → 不打扰
      if (s.configured) {
        setOpen(false);
        return;
      }
      // 本次会话已跳过 → 不打扰
      if (sessionStorage.getItem(DISMISS_KEY) === '1') {
        setOpen(false);
        return;
      }

      // 预填当前值，方便用户看到「默认是什么」再改
      const preset = PROVIDERS.find((p) => p.value === s.provider) || PROVIDERS[0];
      form.setFieldsValue({
        provider: s.provider || preset.value,
        api_key: '',
        api_base_url: s.base_url || preset.base,
        llm_model: s.model || preset.model,
      });
      setOpen(true);
    } catch {
      // 读不到状态就不弹，避免因为接口问题挡住使用
      setOpen(false);
    } finally {
      setChecking(false);
    }
  }, [form]);

  useEffect(() => {
    load();
  }, [load]);

  const handleProviderChange = (v: string) => {
    const p = PROVIDERS.find((x) => x.value === v);
    if (p) {
      form.setFieldsValue({
        api_base_url: p.base,
        llm_model: p.model || form.getFieldValue('llm_model') || '',
      });
    }
    setTestResult(null);
  };

  const testConnection = async () => {
    const v = await form.validateFields(['provider', 'api_key', 'api_base_url', 'llm_model']);
    setTesting(true);
    setTestResult(null);
    try {
      const r = await settingsApi.testApiConnection({
        provider: v.provider,
        api_key: v.api_key,
        api_base_url: v.api_base_url || undefined,
        llm_model: v.llm_model,
      });
      if (r?.success) {
        const ms = r.response_time_ms;
        setTestResult({
          ok: true,
          text: ms != null
            ? `连接成功（${(ms / 1000).toFixed(2)} 秒）`
            : '连接成功',
        });
      } else {
        setTestResult({ ok: false, text: r?.message || '连接失败，请检查 Key 与地址' });
      }
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      setTestResult({
        ok: false,
        text: err?.response?.data?.detail || err?.message || '连接失败',
      });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    const v = await form.validateFields();
    setSaving(true);
    try {
      await settingsApi.updateSettings({
        api_provider: v.provider,
        api_key: v.api_key,
        api_base_url: v.api_base_url || undefined,
        llm_model: v.llm_model,
      });
      msgApi.success('已保存。生成类功能现在会使用这个模型。');
      setOpen(false);
      sessionStorage.setItem(DISMISS_KEY, '1');
      await load();
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      msgApi.error(`保存失败：${err?.response?.data?.detail || err?.message}`);
    } finally {
      setSaving(false);
    }
  };

  const skip = () => {
    sessionStorage.setItem(DISMISS_KEY, '1');
    setOpen(false);
    msgApi.info('已跳过。生成类功能会使用规则推断，随时可在「API 设置」里配置。');
  };

  if (checking) return null;

  return (
    <>
      {ctxHolder}
      <Modal
        open={open}
        title={
          <Space>
            <ApiOutlined />
            <span>配置 AI 模型</span>
          </Space>
        }
        width={560}
        onCancel={skip}
        footer={[
          <Button key="skip" onClick={skip}>
            暂时跳过
          </Button>,
          <Button key="test" onClick={testConnection} loading={testing}>
            测试连接
          </Button>,
          <Button key="save" type="primary" onClick={save} loading={saving}>
            保存并开始
          </Button>,
        ]}
        maskClosable={false}
      >
        {status && (
          <Alert
            type={status.reason.includes('占位') ? 'warning' : 'info'}
            showIcon
            icon={<WarningOutlined />}
            style={{ marginBottom: 16 }}
            message={status.configured ? '当前配置可用' : '尚未配置 AI 模型'}
            description={
              <Text style={{ fontSize: 12 }}>
                {status.configured
                  ? `当前使用 ${status.provider} / ${status.model}`
                  : `${status.reason}。不配置也能用：拆书导入、书源抓取、文本重塑的本地规则都不依赖 AI；` +
                    `但章节生成、拆书增强、子任务分析会回退到规则推断，产出质量有限。`}
              </Text>
            }
          />
        )}

        <Form form={form} layout="vertical" size="middle">
          <Form.Item
            name="provider"
            label="服务商"
            rules={[{ required: true, message: '请选择服务商' }]}
          >
            <Select options={PROVIDERS.map((p) => ({ value: p.value, label: p.label }))}
                    onChange={handleProviderChange} />
          </Form.Item>

          <Form.Item
            name="api_key"
            label="API Key"
            rules={[{ required: true, message: '请填入 API Key' }]}
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                仅保存在本机数据库，不会外传。
              </Text>
            }
          >
            <Input.Password
              prefix={<KeyOutlined />}
              placeholder="sk-..."
              autoComplete="off"
            />
          </Form.Item>

          <Form.Item
            name="api_base_url"
            label="接口地址"
            extra={
              <Text type="secondary" style={{ fontSize: 12 }}>
                填服务商的 OpenAI 兼容地址。用官方地址时保持默认即可。
              </Text>
            }
          >
            <Input placeholder="https://api.openai.com/v1" />
          </Form.Item>

          <Form.Item
            name="llm_model"
            label="模型名称"
            rules={[{ required: true, message: '请填写模型名称' }]}
          >
            <Input placeholder="gpt-4o-mini" />
          </Form.Item>
        </Form>

        {testing && (
          <div style={{ textAlign: 'center', padding: 8 }}>
            <Spin size="small" /> <Text type="secondary">正在测试…</Text>
          </div>
        )}

        {testResult && (
          <Alert
            type={testResult.ok ? 'success' : 'error'}
            showIcon
            icon={testResult.ok ? <CheckCircleOutlined /> : <WarningOutlined />}
            message={testResult.text}
          />
        )}

        <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 16, marginBottom: 0 }}>
          配好后可随时在左侧「API 设置」里更改；想换服务商也在这里改。
        </Paragraph>
      </Modal>
    </>
  );
}
