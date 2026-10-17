import { env } from 'cloudflare:workers';
import { queryPublishedReports } from './biweekly-report';

export async function readNotionReports(studentId: string) {
  const config = env as unknown as Record<string, string>;
  const token = config.NOTION_API_TOKEN || config.NOTION_TOKEN;
  const sourceId = config.NOTION_REPORTS_SOURCE_ID;
  if (!token || !sourceId || !/^[a-f0-9-]{32,36}$/i.test(sourceId)) {
    throw new Error('리포트 연결 설정을 확인해야 합니다.');
  }
  return queryPublishedReports(studentId, async body => {
    const response = await fetch(`https://api.notion.com/v1/data_sources/${sourceId}/query`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Notion-Version': '2025-09-03', 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(20000), cache: 'no-store',
    });
    if (!response.ok) throw new Error('리포트를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.');
    return response.json();
  });
}
