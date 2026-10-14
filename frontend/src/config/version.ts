/**
 * 应用版本信息配置
 *
 * 本项目是基于上游开源作品的二次开发版本，使用自有版本号，
 * 与上游项目的版本序列相互独立。
 *
 * 注意：版本号从 package.json 自动读取，无需手动维护。
 */

/** 二次开发版本号（自维护，不跟随上游） */
export const SECONDARY_DEV_VERSION = '2.0.0';

export const VERSION_INFO = {
  // 应用版本号：构建时由 Vite 注入 package.json 中的自有版本号
  version: import.meta.env.VITE_APP_VERSION || SECONDARY_DEV_VERSION,

  // 构建时间（将在构建时由 Vite 注入）
  buildTime: import.meta.env.VITE_BUILD_TIME || new Date().toISOString().split('T')[0],

  // 项目信息
  projectName: 'Wenshu',
  projectFullName: '文枢 · AI 小说创作工作台',

  // 二次开发声明
  isSecondaryDevelopment: true,
  secondaryDevNotice: '本项目为二次开发版本',
  // 上游来源（署名，非推广链接）
  basedOnProject: 'MuMuAINovel',
  basedOnAuthor: 'xiamuceer-j',
  basedOnUrl: 'https://github.com/xiamuceer-j/MuMuAINovel',
  basedOnLicense: 'GPL-3.0',

  // 许可证
  license: 'AGPL-3.0',
  licenseUrl: 'https://www.gnu.org/licenses/agpl-3.0.html',

  // 维护者（二次开发）
  author: '二次开发维护者',
};

/**
 * 获取格式化的版本信息
 */
export const getVersionString = () => {
  return `v${VERSION_INFO.version}`;
};

/**
 * 获取二次开发声明文案
 */
export const getSecondaryDevNotice = () => {
  return `${VERSION_INFO.secondaryDevNotice} · 基于 ${VERSION_INFO.basedOnProject}`;
};

/**
 * 获取完整的版本描述
 */
export const getFullVersionInfo = () => {
  return `${VERSION_INFO.projectName} ${getVersionString()} - Build ${VERSION_INFO.buildTime}（${VERSION_INFO.secondaryDevNotice}）`;
};
