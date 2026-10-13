"""认证中间件 —— 本地单用户模式。

本工程定位为**本地单人使用**，不需要多用户鉴权。

改造说明（相对上游）：
- 上游从签名会话 Cookie 中解析用户，未登录时 `user_id = None`，
  各接口据此返回 401，前端跳转登录页。
- 本工程在未取到会话时，回退为固定的本地用户身份，使接口可直接使用。

保留的原有行为：
- 若请求确实带了有效会话 Cookie，仍按该用户身份处理（便于将来恢复多用户）。
- 提示词工坊的代理请求逻辑不变。

如需恢复鉴权，见 docs/无登录改造.md。
"""
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.logger import get_logger
from app.security import verify_session_token
from app.user_manager import user_manager

logger = get_logger(__name__)

# 本地默认身份。上游 local 用户的 user_id 形如 local_<md5(username)[:16]>，
# 这里沿用同样的形态，便于与既有数据保持一致。
import hashlib

LOCAL_DEFAULT_USERNAME = "local"
LOCAL_DEFAULT_USER_ID = f"local_{hashlib.md5(LOCAL_DEFAULT_USERNAME.encode()).hexdigest()[:16]}"
LOCAL_DEFAULT_DISPLAY_NAME = "本机用户"

# 本地用户对象缓存：避免每个请求都查库。
_local_user_cache = {"user": None}


async def _ensure_local_user():
    """取得（必要时创建）本地默认用户，返回 User 对象。

    上游的 require_login 依赖检查的是 request.state.user 对象而非 user_id，
    所以这里必须给出真实的 User，而不是仅设一个 id。
    """
    if _local_user_cache["user"] is not None:
        return _local_user_cache["user"]

    try:
        user = await user_manager.get_user(LOCAL_DEFAULT_USER_ID)
    except Exception as e:
        logger.warning(f"查询本地用户失败: {e}")
        user = None

    if user is None:
        try:
            # 复用上游的创建逻辑：传 local_ 前缀的 id 会被识别为本地用户并置为管理员
            user = await user_manager.create_or_update_from_linuxdo(
                linuxdo_id=LOCAL_DEFAULT_USER_ID,
                username=LOCAL_DEFAULT_USERNAME,
                display_name=LOCAL_DEFAULT_DISPLAY_NAME,
                avatar_url=None,
                trust_level=9,
            )
        except Exception as e:
            logger.error(f"创建本地用户失败: {e}")
            user = None

    _local_user_cache["user"] = user
    return user


class AuthMiddleware(BaseHTTPMiddleware):
    """认证中间件（本地单用户模式）"""

    async def dispatch(self, request: Request, call_next):
        # --- 提示词工坊的跨实例代理请求：逻辑不变 ---
        instance_id = request.headers.get("X-Instance-ID")
        is_workshop_path = request.url.path.startswith("/api/prompt-workshop")

        if instance_id and is_workshop_path:
            header_user_id = request.headers.get("X-User-ID")
            request.state.is_proxy_request = True
            request.state.proxy_instance_id = instance_id
            request.state.user_id = header_user_id or LOCAL_DEFAULT_USER_ID
            request.state.user = None
            request.state.is_admin = False
            return await call_next(request)

        request.state.is_proxy_request = False
        request.state.proxy_instance_id = None

        # --- 常规请求：先尝试真实会话 ---
        user_id = verify_session_token(request.cookies.get("session_token"))

        if user_id:
            user = await user_manager.get_user(user_id)
            if user and user.trust_level != -1:
                request.state.user_id = user_id
                request.state.user = user
                request.state.is_admin = user.is_admin
                return await call_next(request)

        # --- 无有效会话：回退本地身份（本工程的默认路径）---
        local_user = await _ensure_local_user()
        request.state.user_id = LOCAL_DEFAULT_USER_ID
        request.state.user = local_user
        request.state.is_admin = True
        return await call_next(request)
