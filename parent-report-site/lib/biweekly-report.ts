type Property = {
  type?: string; number?: number | null; checkbox?: boolean;
  rich_text?: { plain_text?: string; text?: { content?: string } }[];
  formula?: { type: string; string?: string | null };
  date?: { start: string; end?: string | null } | null;
  relation?: { id: string }[]; has_more?: boolean;
};
export type NotionReportPage = { archived?: boolean; in_trash?: boolean; properties: Record<string, Property> };
export const normalizedId = (id: string) => id.replaceAll('-', '').toLowerCase();
export function mapPublishedReport(page: NotionReportPage, studentId: string) {
  const p = page.properties;
  const relation = p['학생'];
  if (page.archived || page.in_trash || p['게시 여부']?.checkbox !== true || relation?.has_more ||
      relation?.relation?.length !== 1 || normalizedId(relation.relation[0].id) !== normalizedId(studentId)) return null;
  const period = p['보고 기간']?.date;
  if (!period?.start || !period.end) return null;
  const numeric = (name: string) => {
    const value = p[name]?.number;
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  };
  const text = (name: string) => {
    const prop = p[name];
    if (prop?.formula?.type === 'string') return prop.formula.string || '';
    return prop?.rich_text?.map(item => item.plain_text ?? item.text?.content ?? '').join('') || '';
  };
  const records = [`보고 기간: ${period.start} ~ ${period.end}`];
  if (p['기록 기준일']?.date?.start) records.push(`기록 기준일: ${p['기록 기준일'].date.start}`);
  const metrics: Record<string, number | null> = {};
  for (const name of ['교재단어 평균', '학원단어 평균', '교재단어 평가수', '학원단어 평가수', '결석일수', '재시험 완료수']) {
    metrics[name] = numeric(name);
    records.push(`${name}: ${metrics[name] ?? '미기록'}`);
  }
  records.push('결석일수는 결석 체크된 날짜 기준입니다.');
  if (text('백지 등급분포')) records.push(`백지 등급분포: ${text('백지 등급분포')}`);
  for (const name of ['백지 평가수', '백지 미기록수']) records.push(`${name}: ${numeric(name) ?? '미기록'}`);
  for (const name of ['학부모 안내문 초안', '교사 의견', '다음 목표']) {
    if (text(name)) records.push(`${name === '학부모 안내문 초안' ? '학습 안내' : name}: ${text(name)}`);
  }
  const homework = [
    ...(text('과제 등급분포') ? [`과제 등급분포: ${text('과제 등급분포')}`] : []),
    `과제 평가수: ${numeric('과제 평가수') ?? '미기록'}`,
    `과제 미기록수: ${numeric('과제 미기록수') ?? '미기록'}`,
    '미기록은 미실시와 미입력을 구분할 수 없습니다.',
  ];
  return { weekStart: period.start, report: { records, metrics, periodEnd: period.end, lessons: [] as string[], homework } };
}

type QueryResponse = { results: NotionReportPage[]; has_more: boolean; next_cursor?: string | null };
export async function queryPublishedReports(studentId: string, query: (body: Record<string, unknown>) => Promise<QueryResponse>) {
  if (!/^[a-f0-9]{32}$/i.test(normalizedId(studentId))) return [];
  const reports = [];
  const cursors = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await query({ page_size: 100, filter: { and: [
      { property: '학생', relation: { contains: studentId } },
      { property: '게시 여부', checkbox: { equals: true } },
    ] }, sorts: [{ property: '보고 기간', direction: 'descending' }], ...(cursor ? { start_cursor: cursor } : {}) });
    for (const row of page.results) {
      const report = mapPublishedReport(row, studentId);
      if (report) reports.push(report);
    }
    if (!page.has_more) break;
    if (!page.next_cursor || cursors.has(page.next_cursor)) throw new Error('리포트 조회를 완료하지 못했습니다.');
    cursor = page.next_cursor;
    cursors.add(cursor);
  } while (cursor);
  return reports.sort((a, b) => b.weekStart.localeCompare(a.weekStart));
}
