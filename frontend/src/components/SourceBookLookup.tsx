import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Alert, Button, Card, Empty, Input, Modal, Skeleton, Space, Tag, Typography, theme } from 'antd';
import { BookOutlined, CloudDownloadOutlined, ReadOutlined, SearchOutlined } from '@ant-design/icons';
import { sourceApi } from '../services/api';
import type { SourceBookInfo, SourceBookResponse, SourceBookSearchResponse } from '../types';
import './SourceBookLookup.css';

const { Text, Title, Paragraph } = Typography;
type SearchBook = SourceBookSearchResponse['items'][number];

function bookReference(input: string): { id: string; fromReader: boolean } | null {
  if (/^\d{15,22}$/.test(input)) return { id: input, fromReader: false };
  const link = input.match(/(?:https?:\/\/)?(?:www\.)?fanqienovel\.com\/(page|reader)\/(\d+)/i);
  return link ? { id: link[2], fromReader: link[1].toLowerCase() === 'reader' } : null;
}

function countText(value: number): string {
  return value >= 10000 ? `${(value / 10000).toFixed(1)}万` : value.toLocaleString();
}

export default function SourceBookLookup({ onSelect }: { onSelect: (book: SourceBookInfo) => void }) {
  const { token } = theme.useToken();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchBook[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [selected, setSelected] = useState<{ id: string; fromReader: boolean } | null>(null);
  const [detail, setDetail] = useState<SourceBookResponse | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [coverFailed, setCoverFailed] = useState(false);
  const searchRequest = useRef(0);
  const detailRequest = useRef(0);

  useEffect(() => () => {
    searchRequest.current += 1;
    detailRequest.current += 1;
  }, []);

  const closeDetail = () => {
    detailRequest.current += 1;
    setSelected(null);
    setLoadingDetail(false);
  };

  const openBook = async (reference: { id: string; fromReader: boolean }) => {
    const request = ++detailRequest.current;
    setSelected(reference);
    setDetail(null);
    setLoadingDetail(true);
    setDetailError(false);
    setCoverFailed(false);
    try {
      const response = await sourceApi.book(reference.id, 1.2, reference.fromReader);
      if (request === detailRequest.current) setDetail(response);
    } catch {
      if (request === detailRequest.current) setDetailError(true);
    } finally {
      if (request === detailRequest.current) setLoadingDetail(false);
    }
  };

  const search = async () => {
    const keyword = query.trim();
    if (!keyword) return;
    const request = ++searchRequest.current;
    setSearchError('');
    setResults([]);
    const reference = bookReference(keyword);
    if (reference) {
      setSearched(false);
      setSearching(false);
      await openBook(reference);
      return;
    }
    closeDetail();
    setSearched(true);
    if (/https?:\/\//i.test(keyword)) {
      setSearching(false);
      setSearchError('请输入番茄书籍或阅读页链接');
      return;
    }
    setSearching(true);
    try {
      const response = await sourceApi.search(keyword);
      if (request === searchRequest.current) setResults(response.items);
    } catch {
      if (request === searchRequest.current) setSearchError('书名搜索暂时不可用');
    } finally {
      if (request === searchRequest.current) setSearching(false);
    }
  };

  const book = detail?.book;
  return (
    <>
      <Card className="source-lookup" title={<Space><SearchOutlined />查找书籍</Space>}>
        <form onSubmit={(event) => { event.preventDefault(); void search(); }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              aria-label="书名或番茄链接"
              placeholder="输入书名、作者，或粘贴番茄书籍链接 / ID"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              allowClear
              size="large"
            />
            <Button htmlType="submit" type="primary" size="large" icon={<SearchOutlined />} loading={searching} disabled={!query.trim()}>
              查找
            </Button>
          </Space.Compact>
        </form>
        <Paragraph type="secondary" className="source-lookup-hint">按书名搜索后选择作品；粘贴书籍或阅读页链接可直接打开详情。</Paragraph>
        {searching ? <Skeleton active paragraph={{ rows: 3 }} /> : searchError ? (
          <Alert type="error" showIcon message={searchError} description="可以输入书名重试，或粘贴番茄书籍链接直接查看详情。" />
        ) : searched && !results.length ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有找到相关书籍，试试完整书名或作者名" />
        ) : results.length > 0 && (
          <ul className="source-lookup-results" aria-label="书籍搜索结果">
            {results.map((item) => (
              <li key={item.book_id}>
                <button type="button" onClick={() => void openBook({ id: item.book_id, fromReader: false })} aria-label={`查看《${item.title}》详情`} aria-haspopup="dialog">
                  <BookOutlined />
                  <span><strong>{item.title}</strong><Text type="secondary">{item.author || '作者未提供'}</Text></span>
                  <span className="source-lookup-open">查看详情</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Modal
        title="书籍详情"
        open={!!selected}
        onCancel={closeDetail}
        width={720}
        rootClassName="source-lookup-modal"
        destroyOnHidden
        styles={{ body: { '--lookup-muted': token.colorTextSecondary, '--lookup-fill': token.colorFillSecondary } as CSSProperties }}
        footer={[
          <Button key="close" onClick={closeDetail}>关闭</Button>,
          <Button
            key="choose"
            type="primary"
            icon={<CloudDownloadOutlined />}
            disabled={!book || loadingDetail}
            onClick={() => { if (book) { onSelect(book); closeDetail(); } }}
          >填入下载管理器</Button>,
        ]}
      >
        {loadingDetail ? <Skeleton active avatar={{ shape: 'square', size: 100 }} paragraph={{ rows: 5 }} /> : book ? (
          <>
            <div className="source-lookup-detail-heading">
              <div className="source-lookup-cover">
                {book.cover_url && !coverFailed ? (
                  <img src={book.cover_url} alt={`${book.title}封面`} referrerPolicy="no-referrer" onError={() => setCoverFailed(true)} />
                ) : <span role="img" aria-label="暂无封面"><BookOutlined />暂无封面</span>}
              </div>
              <div className="source-lookup-detail-title">
                <Title level={3}>{book.title}</Title>
                <Text type="secondary">{book.author || '作者未提供'}</Text>
                <Space size={[4, 8]} wrap className="source-lookup-tags">
                  {book.status && <Tag color={book.status === '完结' ? 'green' : 'cyan'}>{book.status}</Tag>}
                  {[...new Set([book.category, ...book.tags].filter(Boolean))].map((tag) => <Tag key={tag}>{tag}</Tag>)}
                </Space>
                <Text type="secondary">{countText(book.word_count)}字 · {countText(book.read_count)}人在读</Text>
                <Button type="link" icon={<ReadOutlined />} href={`https://fanqienovel.com/page/${encodeURIComponent(book.book_id)}`} target="_blank" rel="noopener noreferrer">在番茄阅读</Button>
              </div>
            </div>
            <section aria-label="作品简介" className="source-lookup-intro">
              <Title level={5}>作品简介</Title>
              <Paragraph>{book.abstract?.trim() || '暂无简介'}</Paragraph>
            </section>
          </>
        ) : detailError && selected ? (
          <Alert type="error" showIcon message="暂时无法打开书籍详情" description="请确认书籍链接或 ID，也可以稍后重试。" action={<Button onClick={() => void openBook(selected)}>重试</Button>} />
        ) : null}
      </Modal>
    </>
  );
}
