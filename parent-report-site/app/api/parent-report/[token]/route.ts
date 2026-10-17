import { getParentReport } from "@/lib/parent-report";

export const dynamic = "force-dynamic";

export async function GET(_: Request, { params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  let report;
  try { report = await getParentReport(token); } catch {
    return Response.json({message: "리포트를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."}, {status:502,headers:{"Cache-Control":"no-store"}});
  }
  if (!report) return Response.json({ message: "리포트를 찾을 수 없습니다." }, { status: 404, headers: { "Cache-Control": "private, no-store" } });
  return Response.json(report, { headers: { "Cache-Control": "private, no-store" } });
}
