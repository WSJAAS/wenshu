import type { ReactNode } from 'react';

/**
 * 路由守卫 —— 本地单用户模式。
 *
 * 改造说明（相对上游）：
 * 上游会调用 authApi.getCurrentUser() 判断登录态，未登录则跳转 /login。
 * 本工程定位为本地单人使用，后端中间件已回退为默认本地身份，
 * 因此这里直接放行，不再做登录检查。
 *
 * 如需恢复鉴权，见 docs/无登录改造.md。
 */
interface ProtectedRouteProps {
  children: ReactNode;
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  return <>{children}</>;
}
