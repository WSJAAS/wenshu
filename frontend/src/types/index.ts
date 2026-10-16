// 用户类型定义
export interface User {
  user_id: string;
  username: string;
  display_name: string;
  avatar_url?: string;
  trust_level: number;
  is_admin: boolean;
  linuxdo_id: string;
  created_at: string;
  last_login: string;
}

export interface EmailLoginPayload {
  email: string;
  code: string;
}

export interface EmailRegisterPayload {
  email: string;
  code: string;
  password: string;
  display_name?: string;
}

export interface EmailSendCodePayload {
  email: string;
  scene: 'register' | 'login' | 'reset_password';
}

export interface EmailResetPasswordPayload {
  email: string;
  code: string;
  new_password: string;
}

export interface SystemSMTPSettings {
  id: string;
  user_id: string;
  smtp_provider: string;
  smtp_host?: string;
  smtp_port: number;
  smtp_username?: string;
  smtp_password?: string;
  smtp_use_tls: boolean;
  smtp_use_ssl: boolean;
  smtp_from_email?: string;
  smtp_from_name: string;
  email_auth_enabled: boolean;
  email_register_enabled: boolean;
  verification_code_ttl_minutes: number;
  verification_resend_interval_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface SystemSMTPSettingsUpdate {
  smtp_provider?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_username?: string;
  smtp_password?: string;
  smtp_use_tls?: boolean;
  smtp_use_ssl?: boolean;
  smtp_from_email?: string;
  smtp_from_name?: string;
  email_auth_enabled?: boolean;
  email_register_enabled?: boolean;
  verification_code_ttl_minutes?: number;
  verification_resend_interval_seconds?: number;
}

// 设置类型定义
export interface Settings {
  id: string;
  user_id: string;
  api_provider: string;
  api_key: string;
  api_base_url: string;
  llm_model: string;
  temperature: number;
  max_tokens: number;
  system_prompt?: string;
  disable_thinking?: boolean;
  cover_api_provider?: string;
  cover_api_key?: string;
  cover_api_base_url?: string;
  cover_image_model?: string;
  cover_enabled?: boolean;
  preferences?: string;
  created_at: string;
  updated_at: string;
}

export interface SettingsUpdate {
  api_provider?: string;
  api_key?: string;
  api_base_url?: string;
  llm_model?: string;
  temperature?: number;
  max_tokens?: number;
  system_prompt?: string;
  disable_thinking?: boolean;
  cover_api_provider?: string;
  cover_api_key?: string;
  cover_api_base_url?: string;
  cover_image_model?: string;
  cover_enabled?: boolean;
  preferences?: string;
}

/**
 * AI 配置状态。
 *
 * 只看「配没配」，不代表 key 真的有效 —— 那需要发一次真实请求验证。
 * 占位值（如 sk-SET_ME）会被后端识别为未配置。
 */
export interface ApiConfigStatus {
  configured: boolean;
  provider: string;
  model: string;
  base_url: string;
  /** 打码后的 key，仅用于展示 */
  key_preview: string;
  /** 未配置的原因，已配置时为空串 */
  reason: string;
}

// API预设相关类型定义
export interface APIKeyPresetConfig {
  api_provider: string;
  api_key: string;
  api_base_url?: string;
  llm_model: string;
  temperature: number;
  max_tokens: number;
  system_prompt?: string;
}

export interface APIKeyPreset {
  id: string;
  name: string;
  description?: string;
  is_active: boolean;
  created_at: string;
  config: APIKeyPresetConfig;
}

export interface PresetCreateRequest {
  name: string;
  description?: string;
  config: APIKeyPresetConfig;
}

export interface PresetUpdateRequest {
  name?: string;
  description?: string;
  config?: APIKeyPresetConfig;
}

export interface PresetListResponse {
  presets: APIKeyPreset[];
  total: number;
  active_preset_id?: string;
  chapter_analysis_preset_id?: string;
}

// LinuxDO 授权 URL 响应
export interface AuthUrlResponse {
  auth_url: string;
  state: string;
}

// 项目类型定义
export interface Project {
  id: string;  // UUID字符串
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  current_words: number;
  status: 'planning' | 'writing' | 'revising' | 'completed';
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
  outline_mode: 'one-to-one' | 'one-to-many';  // 大纲章节模式
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
  chapter_count?: number;
  narrative_perspective?: string;
  character_count?: number;
  cover_image_url?: string;
  cover_prompt?: string;
  cover_status?: 'none' | 'generating' | 'ready' | 'failed';
  cover_error?: string;
  cover_updated_at?: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  outline_mode?: 'one-to-one' | 'one-to-many';  // 大纲章节模式,默认one-to-many
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
}

export interface ProjectUpdate {
  title?: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  status?: 'planning' | 'writing' | 'revising' | 'completed';
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
  chapter_count?: number;
  narrative_perspective?: string;
  character_count?: number;
  // current_words 由章节内容自动计算，不在此接口中
}

// 向导专用的项目更新接口，包含向导流程控制字段
export interface ProjectWizardUpdate extends ProjectUpdate {
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
}

// 项目创建向导
export interface ProjectWizardRequest {
  title: string;
  theme: string;
  genre?: string;
  chapter_count: number;
  narrative_perspective: string;
  character_count?: number;
  target_words?: number;
  outline_mode?: 'one-to-one' | 'one-to-many';  // 大纲章节模式
  world_building?: {
    time_period: string;
    location: string;
    atmosphere: string;
    rules: string;
  };
}

export interface WorldBuildingResponse {
  project_id: string;
  time_period: string;
  location: string;
  atmosphere: string;
  rules: string;
}

// 大纲类型定义
export interface Outline {
  id: string;
  project_id: string;
  title: string;
  content: string;
  structure?: string;
  order_index: number;
  has_chapters?: boolean;
  created_at: string;
  updated_at: string;
}

export interface OutlineCreate {
  project_id: string;
  title: string;
  content: string;
  structure?: string;
  order_index: number;
}

export interface OutlineUpdate {
  title?: string;
  content?: string;
  structure?: string;  // 支持修改structure字段
  // order_index 只能通过 reorder 接口批量调整
}

export interface AgentConversation {
  id: string;
  user_id: string;
  project_id: string;
  title: string;
  summary?: string;
  status: string;
  last_message_at: string;
  created_at: string;
  updated_at: string;
}

export interface AgentMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'tool' | 'system';
  content: string;
  model?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  created_at: string;
}

export interface AgentToolCall {
  id: string;
  conversation_id: string;
  message_id?: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  risk_level: number;
  requires_confirmation: boolean;
  status: string;
  preview?: {
    entity_type: string;
    entity_id: string;
    label: string;
    changes: Record<string, { before: unknown; after: unknown }>;
    resources: string[];
  };
  result?: Record<string, unknown>;
  error_message?: string;
  created_at: string;
}

export interface AgentExecutionStep {
  id: string;
  conversation_id: string;
  user_message_id?: string;
  assistant_message_id?: string;
  tool_call_id?: string;
  sequence: number;
  step_type: 'thought' | 'tool' | 'skill' | string;
  category: 'analysis' | 'project' | 'mcp' | 'skill' | string;
  title: string;
  content?: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'rejected' | 'waiting_confirmation' | string;
  detail?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentConversationDetail extends AgentConversation {
  messages: AgentMessage[];
  tool_calls: AgentToolCall[];
  execution_steps: AgentExecutionStep[];
}

export interface AgentToolDecision {
  success: boolean;
  message: string;
  tool_call: AgentToolCall;
  resources: string[];
}

export type OutlineImportMode = 'append' | 'merge';

export interface OutlineImportPreview {
  valid: boolean;
  version: string;
  source_type: 'outlines' | 'project' | 'unknown';
  source_project?: {
    title: string;
    outline_mode: 'one-to-one' | 'one-to-many';
  } | null;
  target_outline_mode?: 'one-to-one' | 'one-to-many' | null;
  mode: OutlineImportMode;
  statistics: {
    total: number;
    will_create: number;
    will_update: number;
    will_create_chapters: number;
  };
  errors: string[];
  warnings: string[];
}

export interface OutlineImportResult {
  success: boolean;
  message: string;
  mode: OutlineImportMode;
  imported: number;
  updated: number;
  created_chapters: number;
  details: Array<{
    source_order_index: number;
    target_order_index: number;
    title: string;
    action: 'created' | 'updated';
  }>;
  warnings: string[];
}

// 角色类型定义
export interface Character {
  id: string;
  project_id: string;
  name: string;
  age?: string;
  gender?: string;
  is_organization: boolean;
  role_type?: string;
  personality?: string;
  background?: string;
  appearance?: string;
  relationships?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  traits?: string;
  avatar_url?: string;
  // 组织扩展字段（从Organization表关联）
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
  // 角色/组织状态
  status?: string;
  status_changed_chapter?: number;
  current_state?: string;
  state_updated_chapter?: number;
  // 职业相关字段
  main_career_id?: string;
  main_career_stage?: number;
  sub_careers?: Array<{
    career_id: string;
    stage: number;
  }>;
  created_at: string;
  updated_at: string;
}

export interface CharacterUpdate {
  name?: string;
  age?: string;
  gender?: string;
  is_organization?: boolean;
  role_type?: string;
  personality?: string;
  background?: string;
  appearance?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  traits?: string;
  // 组织扩展字段
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
}

// 展开规划数据结构
export interface ExpansionPlanData {
  key_events: string[];
  character_focus: string[];
  emotional_tone: string;
  narrative_goal: string;
  conflict_type: string;
  estimated_words: number;
  scenes?: Array<{
    location: string;
    characters: string[];
    purpose: string;
  }> | null;
}

// 章节类型定义
export interface Chapter {
  id: string;
  project_id: string;
  title: string;
  content?: string;
  summary?: string;
  chapter_number: number;
  word_count: number;
  status: 'draft' | 'writing' | 'completed';
  expansion_plan?: string; // JSON字符串，解析后为ExpansionPlanData
  outline_id?: string; // 关联的大纲ID
  sub_index?: number; // 大纲下的子章节序号
  outline_title?: string; // 大纲标题（从后端联表查询获得）
  outline_order?: number; // 大纲排序序号（从后端联表查询获得）
  created_at: string;
  updated_at: string;
}

export interface ChapterCreate {
  project_id: string;
  title: string;
  chapter_number: number;
  content?: string;
  summary?: string;
  status?: 'draft' | 'writing' | 'completed';
}

export interface ChapterUpdate {
  title?: string;
  content?: string;
  // chapter_number 不允许修改，由大纲顺序决定
  summary?: string;
  // word_count 自动计算，不允许手动修改
  status?: 'draft' | 'writing' | 'completed';
}

// 章节生成请求类型
export interface ChapterGenerateRequest {
  style_id?: number;
  target_word_count?: number;
}

// 章节生成检查响应
export interface ChapterCanGenerateResponse {
  can_generate: boolean;
  reason: string;
  previous_chapters: {
    id: string;
    chapter_number: number;
    title: string;
    has_content: boolean;
    word_count: number;
  }[];
  chapter_number: number;
}

// AI生成请求类型
export interface GenerateOutlineRequest {
  project_id: string;
  genre?: string;
  theme: string;
  chapter_count: number;
  narrative_perspective: string;
  world_context?: Record<string, unknown>;
  characters_context?: Character[];
  target_words?: number;
  requirements?: string;
  provider?: string;
  model?: string;
  // 续写功能新增字段
  mode?: 'auto' | 'new' | 'continue';
  story_direction?: string;
  plot_stage?: 'development' | 'climax' | 'ending';
  keep_existing?: boolean;
}

// 大纲重排序请求类型
export interface OutlineReorderItem {
  id: string;
  order_index: number;
}

export interface OutlineReorderRequest {
  orders: OutlineReorderItem[];
}

// 大纲展开相关类型定义
export interface ChapterPlanItem {
  sub_index: number;
  title: string;
  plot_summary: string;
  key_events: string[];
  character_focus: string[];
  emotional_tone: string;
  narrative_goal: string;
  conflict_type: string;
  estimated_words: number;
  scenes?: Array<{
    location: string;
    characters: string[];
    purpose: string;
  }>;
}

export interface OutlineExpansionRequest {
  target_chapter_count: number;
  expansion_strategy?: 'balanced' | 'climax' | 'detail';
  auto_create_chapters?: boolean;
  provider?: string;
  model?: string;
}

export interface OutlineExpansionResponse {
  outline_id: string;
  outline_title: string;
  target_chapter_count: number;
  actual_chapter_count: number;
  expansion_strategy: string;
  chapter_plans: ChapterPlanItem[];
  created_chapters?: Array<{
    id: string;
    chapter_number: number;
    title: string;
    summary: string;
    outline_id: string;
    sub_index: number;
    status: string;
  }> | null;
}

export interface BatchOutlineExpansionRequest {
  project_id: string;
  outline_ids?: string[];
  chapters_per_outline: number;
  expansion_strategy?: 'balanced' | 'climax' | 'detail';
  auto_create_chapters?: boolean;
  provider?: string;
  model?: string;
}

export interface BatchOutlineExpansionResponse {
  project_id: string;
  total_outlines_expanded: number;
  total_chapters_created: number;
  expansion_results: OutlineExpansionResponse[];
  skipped_outlines?: Array<{
    outline_id: string;
    outline_title: string;
    reason: string;
  }>;
}

export interface GenerateCharacterRequest {
  project_id: string;
  name?: string;
  role_type?: string;
  background?: string;
  requirements?: string;
  provider?: string;
  model?: string;
}

export interface PolishTextRequest {
  /** 原始文本（兼容字段，与 original_text 二者其一） */
  text?: string;
  original_text?: string;
  /** novel / paper */
  mode?: string;
  /** 是否调用模型；false 走本地规则 */
  use_model?: boolean;
  style?: string;
  project_id?: string;
  provider?: string;
  model?: string;
  temperature?: number;
}

export interface PolishDiagnostics {
  [key: string]: unknown;
}

export interface PolishResponse {
  original_text: string;
  polished_text: string;
  word_count_before: number;
  word_count_after: number;
  /** model = 模型改写；local_rules = 本地规则 */
  engine: string;
  engine_note?: string | null;
  applied_rules?: string[] | null;
  diagnostics_before?: PolishDiagnostics | null;
  diagnostics_after?: PolishDiagnostics | null;
  integrity?: Record<string, unknown> | null;
}

export interface PolishBatchResponse {
  total: number;
  results: Array<{
    index: number;
    original: string;
    polished: string;
    word_count_before?: number;
    word_count_after?: number;
    skipped?: boolean;
  }>;
}

export interface AcceptRecord {
  name: string;
  path: string;
  saved_at: string;
  chars: number;
  task_id: string;
}

export interface AcceptResponse {
  ok: boolean;
  saved: AcceptRecord;
  previous: AcceptRecord | null;
  can_rollback: boolean;
  versions: number;
}

// ============================================================
//  书源
// ============================================================
export interface SourceStatus {
  enabled: boolean;
  module: string;
  note: string;
  /** 是否已配置登录 Cookie */
  login_configured?: boolean;
  /** Cookie 里是否含 sessionid（能取全文的关键项） */
  login_has_session?: boolean;
  login_note?: string;
}

export interface SourceLoginState {
  configured: boolean;
  has_sessionid: boolean;
  names: string[];
  note: string;
  saved_to?: string;
}

/** 浏览器联动的安装指引 */
export interface SourceHandoverGuide {
  userscript_url: string;
  steps: string[];
  notes: string[];
  login_ready: boolean;
  login_note: string;
}

/** 浏览器交接的返回 */
export interface SourceHandoverResult {
  ok: boolean;
  book_id: string;
  title: string;
  author: string;
  chapter_total: number;
  status: string;
  category: string;
  word_count: number;
  read_count: number;
  /** 页面上显示的书名（可能带混淆或与平台不一致） */
  from_page_title: string;
  /** 页面书名与平台登记不一致 */
  title_mismatch: boolean;
  login_ready: boolean;
  login_note: string;
  task_id: string | null;
  message: string;
}

export interface SourceRankItem {
  book_id: string;
  /** 干净书名（详情页补取。补取失败时退回榜单页书名，可能带混淆） */
  title: string;
  title_raw?: string;
  title_decoded?: string;
  /** 书名是否可能带字体混淆（详情补取失败时） */
  title_may_be_garbled?: boolean;
  author: string;
  category?: string;
  tags?: string[];
  status?: string;
  read_count: number;
  /** 榜单原始读数，可能与 read_count 不同 */
  read_count_raw?: number;
  read_count_alt?: number;
  word_count: number;
  abstract?: string;
  cover_url?: string;
  last_chapter_title?: string;
  rank_diff?: unknown;
  enriched?: boolean;
  enrich_error?: string;
}

export interface SourceRankResponse {
  items: SourceRankItem[];
  fetched: number;
  /** 平台声明的总数，供采集逻辑判断覆盖范围。 */
  declared_total: number;
  enriched: number;
  enrich_failed: number;
  garbled_titles: number;
  coverage_note: string;
  is_complete: boolean;
  platform_note: string;
}

export interface SourceChapterRef {
  item_id: string;
  title: string;
  volume: string;
  order: number;
  /** 仅参考：不在免费试读段。实测不代表不可读 */
  is_locked: boolean;
  need_pay: boolean;
}

export interface SourceBookInfo {
  book_id: string;
  title: string;
  author: string;
  abstract: string;
  category: string;
  tags: string[];
  word_count: number;
  read_count: number;
  status: string;
  cover_url: string;
  source_url: string;
  last_chapter_title: string;
}

export interface SourceBookSearchResponse {
  items: Array<Pick<SourceBookInfo, 'book_id' | 'title' | 'author'>>;
}

export interface SourceBookResponse {
  book: SourceBookInfo;
  chapters: SourceChapterRef[];
  volumes: Array<{ name: string; count: number }>;
  lock_field_note: string;
}

export interface SourceTask {
  task_id: string;
  status: string;
  message: string;
  error: string;
  book_id: string;
  book_title: string;
  total_chapters: number;
  done_count: number;
  ok_count: number;
  failed_count: number;
  /** 内容指纹，用于验证任务隔离 */
  chapters_digest?: string;
  /** 交接给拆书模块后生成的拆书任务 ID */
  import_task_id?: string;
  /** 交接状态：'' 未交接 / handed 已交接 / failed 失败 */
  import_status?: string;
  import_message?: string;
  imported_at?: string;
  /** 抓完后是否自动交给拆书模块 */
  auto_import?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface SourceChapterResult {
  order: number;
  item_id: string;
  title: string;
  volume: string;
  /**
   * ok        完整取得（长度与平台申报相符）
   * preview   只取得试读片段（未登录时锁定章被服务端截断成约 200 字符）
   * locked / unavailable / garbled / failed / not_fetched
   */
  status: string;
  error: string;
  chars: number;
  pua_before: number;
  pua_after: number;
  /** 平台是否标记该章不在免费试读段 */
  is_locked?: boolean;
  /** 平台申报的该章字数，用于判定截断 */
  claimed_words?: number;
  text?: string;
}

export interface SourceMissingReport {
  total: number;
  ok: number;
  missing_count: number;
  by_status: Record<string, number>;
  /** 只取得试读片段的章数（未登录时锁定章被截断） */
  preview_count?: number;
  /** 这些片段合计多少字 */
  preview_chars?: number;
  missing: Array<{
    order: number;
    item_id: string;
    title: string;
    volume: string;
    status: string;
    error: string;
    chars?: number;
    /** 平台申报字数 */
    claimed_words?: number;
    /** 相对申报还差多少字 */
    missing_words?: number;
  }>;
}

export interface SourceTaskChaptersResponse {
  task: SourceTask;
  chapters: SourceChapterResult[];
  report: SourceMissingReport;
  scope_note: string;
}

export interface SourceSelection {
  preset: 'first' | 'volume' | 'all' | 'ids';
  count: number;
  volume: string;
  item_ids: string[];
}

// ============================================================
//  整本下载（外部下载器）
// ============================================================

/** 外部下载器的章节进度 */
export interface ExtDownloadProgress {
  /** 目录里的总章数 */
  chapter_total: number;
  /** 已落盘的章数 */
  saved_chapters: number;
  /** 已完成的批次数 */
  group_done: number;
  /** 总批次数 */
  group_total: number;
}

/** 外部下载器的任务快照。state: queued / running / done / error */
export interface ExtDownloadJob {
  id?: number;
  state?: string;
  title?: string;
  author?: string;
  /** state === 'error' 时的原因 */
  error?: string;
  progress?: ExtDownloadProgress;
}

/** GET /api/ext-download/status/{job_id} 的返回 */
export interface ExtDownloadStatus {
  job: ExtDownloadJob;
  /** 打包产物的路径，未完成时为 null */
  epub_path?: string | null;
  /** 打包产物的字节数 */
  epub_bytes?: number | null;
}

// ============================================================
//  下载管理器（下载任务落库 + 拆书交接）
// ============================================================

/**
 * 下载管理器里的整本下载任务（对应后端 download_jobs 表）。
 *
 * 与 ExtDownloadJob 的区别：后者是外部下载器的内存快照（不落库），
 * 这里是本系统自己落库的任务记录，多了一句拆书交接三件套
 * （import_task_id / import_status / import_message）与 source_tasks 同款。
 * state: queued / running / done / error / cancelled / lost，
 * 其中 done / error / cancelled / lost 为终态。
 */
export interface DownloadJob {
  id: string;
  book_id: string;
  /** 外部下载器任务号（其 job 表 id，统一存 str） */
  ext_job_id: string;
  state: 'queued' | 'running' | 'done' | 'error' | 'cancelled' | 'lost';
  message: string;
  error: string;
  book_title: string;
  book_author: string;
  chapter_total: number;
  saved_chapters: number;
  group_done: number;
  group_total: number;
  epub_path: string;
  epub_bytes: number;
  txt_chars: number;
  /** 拆书交接任务 id；空串 = 未交接 */
  import_task_id: string;
  /** 交接状态：空串 = 未交接，handed = 已交接，failed = 交接失败 */
  import_status: '' | 'handed' | 'failed';
  import_message: string;
  imported_at: string | null;
  /** 下载完成后是否自动交接给拆书 */
  auto_import: boolean;
  created_at: string | null;
  updated_at: string | null;
}

// 向导API响应类型
export interface GenerateCharactersResponse {
  characters: Character[];
}

export interface GenerateOutlineResponse {
  outlines: Outline[];
}

// API响应类型
export interface ApiResponse<T> {
  data: T;
  message?: string;
}

// 写作风格类型定义
export interface WritingStyle {
  id: number;
  user_id: string | null;  // NULL 表示全局预设风格
  name: string;
  style_type: 'preset' | 'custom';
  preset_id?: string;
  description?: string;
  prompt_content: string;
  is_default: boolean;
  order_index: number;
  created_at: string;
  updated_at: string;
}

export interface WritingStyleCreate {
  name: string;
  style_type?: 'preset' | 'custom';
  preset_id?: string;
  description?: string;
  prompt_content: string;
}

export interface WritingStyleUpdate {
  name?: string;
  description?: string;
  prompt_content?: string;
  order_index?: number;
}

export interface PresetStyle {
  id: string;
  name: string;
  description: string;
  prompt_content: string;
}

export interface WritingStyleListResponse {
  styles: WritingStyle[];
  total: number;
}

export interface PaginationResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// 向导表单数据类型
export interface WizardBasicInfo {
  title: string;
  description: string;
  theme: string;
  genre: string | string[];
  chapter_count: number;
  narrative_perspective: string;
  character_count?: number;
  target_words?: number;
  outline_mode?: 'one-to-one' | 'one-to-many';  // 大纲章节模式
}

// API 错误响应类型
export interface ApiError {
  response?: {
    data?: {
      detail?: string;
    };
  };
  message?: string;
}

// 章节分析任务相关类型
export interface AnalysisTask {
  has_task: boolean;
  task_id: string | null;
  chapter_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'none';
  progress: number;
  error_message?: string | null;
  auto_recovered?: boolean;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface BatchAnalysisStatusResponse {
  project_id: string;
  total: number;
  items: Record<string, AnalysisTask>;
}

export interface BatchAnalyzeUnanalyzedRequest {
  chapter_ids?: string[];
}

export interface BatchAnalyzeUnanalyzedResponse {
  project_id: string;
  total_candidates: number;
  total_started: number;
  total_skipped_no_content: number;
  total_skipped_running: number;
  total_already_completed: number;
  started_tasks: Record<string, AnalysisTask>;
}

// 分析结果 - 钩子
export interface AnalysisHook {
  type: string;
  content: string;
  strength: number;
  position: string;
}

// 分析结果 - 伏笔
export interface AnalysisForeshadow {
  content: string;
  type: 'planted' | 'resolved';
  strength: number;
  subtlety: number;
  reference_chapter?: number;
}

// 分析结果 - 冲突
export interface AnalysisConflict {
  types: string[];
  parties: string[];
  level: number;
  description: string;
  resolution_progress: number;
}

// 分析结果 - 情感曲线
export interface AnalysisEmotionalArc {
  primary_emotion: string;
  intensity: number;
  curve: string;
  secondary_emotions: string[];
}

// 分析结果 - 角色状态
export interface AnalysisCharacterState {
  character_name: string;
  state_before: string;
  state_after: string;
  psychological_change: string;
  key_event: string;
  relationship_changes: Record<string, string>;
}

// 分析结果 - 情节点
export interface AnalysisPlotPoint {
  content: string;
  type: 'revelation' | 'conflict' | 'resolution' | 'transition';
  importance: number;
  impact: string;
}

// 分析结果 - 场景
export interface AnalysisScene {
  location: string;
  atmosphere: string;
  duration: string;
}

// 分析结果 - 评分
export interface AnalysisScores {
  pacing: number;
  engagement: number;
  coherence: number;
  overall: number;
}

// 完整分析数据 - 匹配后端PlotAnalysis模型
export interface AnalysisData {
  id: string;
  chapter_id: string;
  plot_stage: string;
  conflict_level: number;
  conflict_types: string[];
  emotional_tone: string;
  emotional_intensity: number;
  hooks: AnalysisHook[];
  hooks_count: number;
  foreshadows: AnalysisForeshadow[];
  foreshadows_planted: number;
  foreshadows_resolved: number;
  plot_points: AnalysisPlotPoint[];
  plot_points_count: number;
  character_states: AnalysisCharacterState[];
  scenes?: AnalysisScene[];
  pacing: string;
  overall_quality_score: number;
  pacing_score: number;
  engagement_score: number;
  coherence_score: number;
  analysis_report: string;
  suggestions: string[];
  dialogue_ratio: number;
  description_ratio: number;
  created_at: string;
}

// 记忆片段
export interface StoryMemory {
  id: string;
  type: 'hook' | 'foreshadow' | 'plot_point' | 'character_event';
  title: string;
  content: string;
  importance: number;
  tags: string[];
  is_foreshadow: 0 | 1 | 2; // 0=普通, 1=已埋下, 2=已回收
}

export interface EntityChangesSummaryItem {
  updated_count?: number;
  state_updated_count?: number;
  relationship_created_count?: number;
  relationship_updated_count?: number;
  org_updated_count?: number;
  changes: string[];
}

// 章节分析结果响应 - 匹配后端API返回
export interface ChapterAnalysisResponse {
  chapter_id: string;
  analysis: AnalysisData;  // 注意：后端返回的是analysis而不是analysis_data
  memories: StoryMemory[];
  created_at: string;
  entity_changes?: {
    careers: EntityChangesSummaryItem;
    character_states: EntityChangesSummaryItem;
    organization_states: EntityChangesSummaryItem;
  };
}

// 手动触发分析响应
export interface TriggerAnalysisResponse {
  task_id: string;
  chapter_id: string;
  status: string;
  message: string;
}

// MCP 插件类型定义 - 优化后只包含必要字段
export interface MCPPlugin {
  id: string;
  plugin_name: string;
  display_name: string;
  description?: string;
  plugin_type: 'http' | 'stdio' | 'streamable_http' | 'sse';
  category: string;

  // HTTP类型字段
  server_url?: string;
  headers?: Record<string, string>;

  // Stdio类型字段
  command?: string;
  args?: string[];
  env?: Record<string, string>;

  // 状态字段
  enabled: boolean;
  status: 'active' | 'inactive' | 'error';
  last_error?: string;
  last_test_at?: string;

  // 时间戳
  created_at: string;
}

export interface MCPPluginCreate {
  plugin_name: string;
  display_name?: string;
  description?: string;
  server_type: 'http' | 'stdio' | 'streamable_http' | 'sse';
  server_url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface MCPPluginUpdate {
  display_name?: string;
  description?: string;
  server_url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface MCPTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export interface MCPTestResult {
  success: boolean;
  message: string;
  tools?: MCPTool[];
  tools_count?: number;
  response_time_ms?: number;
  error?: string;
  error_type?: string;
  suggestions?: string[];
}

export interface MCPToolCallRequest {
  plugin_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

export interface MCPToolCallResponse {
  success: boolean;
  result?: unknown;
  error?: string;
}

// 伏笔管理类型定义
export type ForeshadowStatus = 'pending' | 'planted' | 'resolved' | 'partially_resolved' | 'abandoned';
export type ForeshadowSourceType = 'analysis' | 'manual';
export type ForeshadowCategory = 'identity' | 'mystery' | 'item' | 'relationship' | 'event' | 'ability' | 'prophecy';

export interface Foreshadow {
  id: string;
  project_id: string;
  title: string;
  content: string;
  hint_text?: string;
  resolution_text?: string;
  source_type?: ForeshadowSourceType;
  source_memory_id?: string;
  source_analysis_id?: string;
  plant_chapter_id?: string;
  plant_chapter_number?: number;
  target_resolve_chapter_id?: string;
  target_resolve_chapter_number?: number;
  actual_resolve_chapter_id?: string;
  actual_resolve_chapter_number?: number;
  status: ForeshadowStatus;
  is_long_term: boolean;
  importance: number;
  strength: number;
  subtlety: number;
  urgency: number;
  related_characters?: string[];
  related_foreshadow_ids?: string[];
  tags?: string[];
  category?: ForeshadowCategory;
  notes?: string;
  resolution_notes?: string;
  auto_remind: boolean;
  remind_before_chapters: number;
  include_in_context: boolean;
  created_at?: string;
  updated_at?: string;
  planted_at?: string;
  resolved_at?: string;
}

export interface ForeshadowCreate {
  project_id: string;
  title: string;
  content: string;
  hint_text?: string;
  resolution_text?: string;
  plant_chapter_number?: number;
  target_resolve_chapter_number?: number;
  is_long_term?: boolean;
  importance?: number;
  strength?: number;
  subtlety?: number;
  related_characters?: string[];
  tags?: string[];
  category?: ForeshadowCategory;
  notes?: string;
  resolution_notes?: string;
  auto_remind?: boolean;
  remind_before_chapters?: number;
  include_in_context?: boolean;
}

export interface ForeshadowUpdate {
  title?: string;
  content?: string;
  hint_text?: string;
  resolution_text?: string;
  plant_chapter_number?: number;
  target_resolve_chapter_number?: number;
  status?: ForeshadowStatus;
  is_long_term?: boolean;
  importance?: number;
  strength?: number;
  subtlety?: number;
  urgency?: number;
  related_characters?: string[];
  related_foreshadow_ids?: string[];
  tags?: string[];
  category?: ForeshadowCategory;
  notes?: string;
  resolution_notes?: string;
  auto_remind?: boolean;
  remind_before_chapters?: number;
  include_in_context?: boolean;
}

export interface ForeshadowStats {
  total: number;
  pending: number;
  planted: number;
  resolved: number;
  partially_resolved: number;
  abandoned: number;
  long_term_count: number;
  overdue_count: number;
}

export interface ForeshadowListResponse {
  total: number;
  items: Foreshadow[];
  stats?: ForeshadowStats;
}

export interface PlantForeshadowRequest {
  chapter_id: string;
  chapter_number: number;
  hint_text?: string;
}

export interface ResolveForeshadowRequest {
  chapter_id: string;
  chapter_number: number;
  resolution_text?: string;
  is_partial?: boolean;
}

export interface SyncFromAnalysisRequest {
  chapter_ids?: string[];
  overwrite_existing?: boolean;
  auto_set_planted?: boolean;
}

export interface SyncFromAnalysisResponse {
  synced_count: number;
  skipped_count: number;
  new_foreshadows: Foreshadow[];
  skipped_reasons: Array<{ source_memory_id: string; reason: string }>;
}

export interface ForeshadowContextResponse {
  chapter_number: number;
  context_text: string;
  pending_plant: Foreshadow[];
  pending_resolve: Foreshadow[];
  overdue: Foreshadow[];
  recently_planted: Foreshadow[];
}

// ==================== 拆书导入类型定义 ====================

export type BookImportTaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export type BookImportWarningLevel = 'info' | 'warning' | 'error';
export type BookImportExtractMode = 'tail' | 'full';

export interface BookImportWarning {
  code: string;
  message: string;
  level: BookImportWarningLevel;
}

export interface BookImportProjectSuggestion {
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  narrative_perspective: string;
  target_words: number;
}

export interface BookImportChapter {
  title: string;
  content: string;
  summary?: string;
  chapter_number: number;
  outline_title?: string;
}

export interface BookImportOutline {
  title: string;
  content?: string;
  order_index: number;
  structure?: Record<string, unknown>;
}

export interface BookImportTask {
  task_id: string;
  status: BookImportTaskStatus;
  progress: number;
  message?: string;
  error?: string;
  created_at: string;
  updated_at: string;
  /**
   * AI 使用情况。
   *
   * 独立于 message 存在，因为 message 会被后续的进度更新覆盖掉：
   * AI 失败时写了「使用规则推断」，但接下来「正在生成大纲」等消息
   * 会把它冲掉，用户最终只看到一句中性提示。
   */
  ai_used?: boolean;
  ai_degraded?: boolean;
  ai_fallback_reasons?: string[];
}

export interface BookImportPreview {
  task_id: string;
  project_suggestion: BookImportProjectSuggestion;
  chapters: BookImportChapter[];
  outlines: BookImportOutline[];
  warnings: BookImportWarning[];
}

export interface BookImportApplyPayload {
  project_suggestion: BookImportProjectSuggestion;
  chapters: BookImportChapter[];
  outlines: BookImportOutline[];
  import_mode?: 'append' | 'overwrite';
}

export interface BookImportCreateTaskPayload {
  file: File;
  extract_mode?: BookImportExtractMode;
  tail_chapter_count?: number;
}

export interface BookImportResult {
  success: boolean;
  project_id: string;
  statistics: {
    chapters: number;
    outlines: number;
    generated_careers?: number;
    generated_entities?: number;
    generated_world_building?: number;
  };
  warnings: BookImportWarning[];
}

export interface BookImportStepFailure {
  step_name: string;       // world_building / career_system / characters
  step_label: string;      // 中文名
  error: string;           // 错误详情
  retry_count?: number;    // 已重试次数
}

export interface BookImportRetryResult {
  success: boolean;
  project_id: string;
  retry_results: Record<string, number>;
  still_failed: BookImportStepFailure[];
}

// ==================== 提示词工坊类型定义 ====================

export interface PromptWorkshopItem {
  id: string;
  name: string;
  description?: string;
  prompt_content: string;
  category: string;
  tags?: string[];
  author_name?: string;
  is_official: boolean;
  download_count: number;
  like_count: number;
  is_liked?: boolean;
  created_at?: string;
}

export interface PromptSubmission {
  id: string;
  name: string;
  description?: string;
  prompt_content?: string;
  category: string;
  tags?: string[];
  author_display_name?: string;
  is_anonymous: boolean;
  status: 'pending' | 'approved' | 'rejected';
  review_note?: string;
  reviewed_at?: string;
  created_at?: string;
  source_instance?: string;
  submitter_name?: string;
}

export interface PromptSubmissionCreate {
  name: string;
  description?: string;
  prompt_content: string;
  category: string;
  tags?: string[];
  author_display_name?: string;
  is_anonymous?: boolean;
  source_style_id?: number;
}

export interface PromptWorkshopCategory {
  id: string;
  name: string;
  count: number;
}

export interface PromptWorkshopListResponse {
  success: boolean;
  data: {
    total: number;
    page: number;
    limit: number;
    items: PromptWorkshopItem[];
    categories: PromptWorkshopCategory[];
  };
}

export interface PromptWorkshopStatusResponse {
  mode: 'client' | 'server';
  instance_id: string;
  cloud_url?: string;
  cloud_connected?: boolean;
}

export interface PromptWorkshopAdminStats {
  total_items: number;
  total_official: number;
  total_pending: number;
  total_downloads: number;
  total_likes: number;
}

// ==================== 公告类型定义 ====================

export type AnnouncementLevel = 'info' | 'success' | 'warning' | 'error';
export type AnnouncementStatus = 'draft' | 'published' | 'hidden';

export interface Announcement {
  id: string;
  title: string;
  content: string;
  summary?: string | null;
  level: AnnouncementLevel;
  status?: AnnouncementStatus;
  pinned: boolean;
  author_id?: string | null;
  author_name?: string | null;
  publish_at?: string | null;
  expire_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AnnouncementCreate {
  title: string;
  content: string;
  summary?: string;
  level?: AnnouncementLevel;
  status?: AnnouncementStatus;
  pinned?: boolean;
  publish_at?: string;
  expire_at?: string;
}

export interface AnnouncementUpdate {
  title?: string;
  content?: string;
  summary?: string;
  level?: AnnouncementLevel;
  status?: AnnouncementStatus;
  pinned?: boolean;
  publish_at?: string | null;
  expire_at?: string | null;
}

export interface AnnouncementListResponse {
  success: boolean;
  data: {
    total: number;
    page: number;
    limit: number;
    items: Announcement[];
    active_ids?: string[];
    latest_updated_at?: string | null;
    server_time?: string;
  };
}

export interface AnnouncementStatusResponse {
  mode: 'client' | 'server' | string;
  instance_id: string;
  cloud_url?: string;
  cloud_connected?: boolean;
}

// 提示词工坊分类常量
export const PROMPT_CATEGORIES: Record<string, string> = {
  general: '通用',
  fantasy: '玄幻/仙侠',
  martial: '武侠',
  romance: '言情',
  scifi: '科幻',
  horror: '悬疑/惊悚',
  history: '历史',
  urban: '都市',
  game: '游戏/电竞',
  other: '其他',
};
