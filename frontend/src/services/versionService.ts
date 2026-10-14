/**
 * 版本信息服务
 *
 * 本项目为二次开发版本，使用自有版本号，
 * 因此不再向上游仓库查询更新，避免把上游版本号当作“新版本”提示。
 */

import { VERSION_INFO } from '../config/version';

export interface VersionCheckResult {
  hasUpdate: boolean;
  latestVersion: string;
  releaseUrl: string;
}

/**
 * 检查是否有更新。
 *
 * 二次开发版本不联网比对上游版本，始终返回“无更新”，
 * 并把当前自有版本号回显给调用方（例如页脚展示）。
 */
export async function checkLatestVersion(): Promise<VersionCheckResult> {
  return {
    hasUpdate: false,
    latestVersion: VERSION_INFO.version,
    releaseUrl: VERSION_INFO.basedOnUrl,
  };
}

/**
 * 检查是否应该执行版本检查（避免频繁请求）
 */
export function shouldCheckVersion(): boolean {
  const lastCheck = localStorage.getItem('version_last_check');

  if (!lastCheck) {
    return true;
  }

  const lastCheckTime = new Date(lastCheck).getTime();
  const now = Date.now();
  const sixHoursMs = 6 * 60 * 60 * 1000; // 6小时

  return now - lastCheckTime >= sixHoursMs;
}

/**
 * 记录版本检查时间
 */
export function markVersionChecked(): void {
  localStorage.setItem('version_last_check', new Date().toISOString());
}

/**
 * 获取缓存的版本信息
 */
export function getCachedVersionInfo(): VersionCheckResult | null {
  const cached = localStorage.getItem('version_check_result');
  if (cached) {
    try {
      return JSON.parse(cached);
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * 缓存版本信息
 */
export function cacheVersionInfo(info: VersionCheckResult): void {
  localStorage.setItem('version_check_result', JSON.stringify(info));
}

/**
 * 用户已查看更新提示
 */
export function markUpdateViewed(version: string): void {
  localStorage.setItem('version_viewed', version);
}

/**
 * 检查用户是否已查看此版本的更新提示
 */
export function hasViewedUpdate(version: string): boolean {
  const viewedVersion = localStorage.getItem('version_viewed');

  // 如果已查看的版本低于最新版本，应该显示红点
  if (viewedVersion && version) {
    const parts1 = viewedVersion.split('.').map(Number);
    const parts2 = version.split('.').map(Number);

    for (let i = 0; i < Math.max(parts1.length, parts2.length); i++) {
      const num1 = parts1[i] || 0;
      const num2 = parts2[i] || 0;

      if (num1 < num2) {
        return false; // 已查看的版本低于最新版本，需要显示红点
      }
      if (num1 > num2) {
        return true; // 已查看的版本高于最新版本
      }
    }
  }

  return viewedVersion === version;
}
