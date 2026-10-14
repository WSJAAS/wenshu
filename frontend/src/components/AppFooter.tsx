import { Typography, Space, Divider, Grid, theme } from 'antd';
import { GithubOutlined, CopyrightOutlined, ClockCircleOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { VERSION_INFO, getVersionString, getSecondaryDevNotice } from '../config/version';

const { Text, Link } = Typography;
const { useBreakpoint } = Grid;

interface AppFooterProps {
  sidebarWidth?: number;
}

/**
 * 底部信息栏。
 *
 * 只保留与运行相关的信息：自有版本号、二次开发声明、
 * 上游署名（许可证要求的署名，非推广位）、许可协议与构建时间。
 */
export default function AppFooter({ sidebarWidth = 0 }: AppFooterProps) {
  const screens = useBreakpoint();
  const isMobile = !screens.md;
  const { token } = theme.useToken();
  const alphaColor = (color: string, alpha: number) => `color-mix(in srgb, ${color} ${(alpha * 100).toFixed(0)}%, transparent)`;

  // 计算左边距：桌面端有侧边栏时需要偏移
  const leftOffset = isMobile ? 0 : sidebarWidth;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 0,
        left: leftOffset,
        right: 0,
        backdropFilter: 'blur(20px) saturate(180%)',
        WebkitBackdropFilter: 'blur(20px) saturate(180%)',
        borderTop: `1px solid ${token.colorBorder}`,
        padding: isMobile ? '8px 12px' : '10px 16px',
        zIndex: 100,
        boxShadow: `0 -2px 16px ${alphaColor(token.colorText, 0.08)}`,
        backgroundColor: alphaColor(token.colorBgContainer, 0.82), // 半透明背景以支持 backdrop-filter
        transition: 'left 0.3s ease', // 平滑过渡
      }}
    >
      <div
        style={{
          maxWidth: 1400,
          margin: '0 auto',
          textAlign: 'center',
        }}
      >
        {isMobile ? (
          // 移动端：紧凑单行布局
          <div style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            gap: 8,
            flexWrap: 'wrap'
          }}>
            <Text
              style={{
                fontSize: 11,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                color: token.colorTextSecondary,
              }}
              title={getSecondaryDevNotice()}
            >
              <strong style={{ color: token.colorText }}>{VERSION_INFO.projectName}</strong>
              <span>{getVersionString()}</span>
            </Text>
            <Divider type="vertical" style={{ margin: '0 4px', borderColor: token.colorBorder }} />
            <Text
              style={{
                fontSize: 11,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                color: token.colorTextTertiary,
              }}
            >
              <InfoCircleOutlined style={{ fontSize: 11 }} />
              {VERSION_INFO.secondaryDevNotice}
            </Text>
            <Divider type="vertical" style={{ margin: '0 4px', borderColor: token.colorBorder }} />
            <Link
              href={VERSION_INFO.basedOnUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: 11,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                color: token.colorTextSecondary,
              }}
              title="上游项目（许可证署名）"
            >
              <GithubOutlined style={{ fontSize: 12 }} />
              <span>基于 {VERSION_INFO.basedOnProject}</span>
            </Link>
            <Text
              style={{
                fontSize: 10,
                color: token.colorTextTertiary,
              }}
            >
              <ClockCircleOutlined style={{ fontSize: 10, marginRight: 4 }} />
              {VERSION_INFO.buildTime}
            </Text>
          </div>
        ) : (
          // PC端：完整布局
          <Space
            direction="horizontal"
            size={12}
            split={<Divider type="vertical" style={{ borderColor: token.colorBorder }} />}
            style={{
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center'
            }}
          >
            {/* 版本信息：自有版本号 */}
            <Text
              style={{
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                color: token.colorTextSecondary,
                textShadow: 'none',
              }}
              title={getSecondaryDevNotice()}
            >
              <strong style={{ color: token.colorText }}>{VERSION_INFO.projectName}</strong>
              <span>{getVersionString()}</span>
            </Text>

            {/* 二次开发声明 */}
            <Text
              style={{
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                color: token.colorTextSecondary,
              }}
            >
              <InfoCircleOutlined style={{ fontSize: 12 }} />
              <span>{VERSION_INFO.secondaryDevNotice}</span>
            </Text>

            {/* 上游署名（许可证要求，非推广入口） */}
            <Link
              href={VERSION_INFO.basedOnUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                color: token.colorTextSecondary,
              }}
            >
              <GithubOutlined style={{ fontSize: 13 }} />
              <span>基于 {VERSION_INFO.basedOnProject}</span>
            </Link>

            {/* 许可证 */}
            <Link
              href={VERSION_INFO.licenseUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                color: token.colorTextSecondary,
              }}
            >
              <CopyrightOutlined style={{ fontSize: 11 }} />
              <span>{VERSION_INFO.license}</span>
            </Link>

            {/* 更新时间 */}
            <Text
              style={{
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                color: token.colorTextTertiary,
              }}
            >
              <ClockCircleOutlined style={{ fontSize: 12 }} />
              <span>{VERSION_INFO.buildTime}</span>
            </Text>
          </Space>
        )}
      </div>

    </div>
  );
}
