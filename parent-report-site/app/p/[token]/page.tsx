"use client";

import { useEffect, useState } from "react";
import { BookOpenCheck, CalendarDays, LockKeyhole, RefreshCw } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type ReportPayload = { student: { displayName: string; className: string; teacherName: string }; reports: { weekStart: string; report: Record<string, unknown> }[] };

function TextList({ value, empty }: { value: unknown; empty: string }) {
  const rows = Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim()) : [];
  if (!rows.length) return <p className="text-sm leading-6 text-slate-500">{empty}</p>;
  return <ul className="space-y-3">{rows.map((item, index) => <li key={`${item}-${index}`} className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-3 text-sm font-medium leading-6 text-slate-800">{item}</li>)}</ul>;
}

export default function ParentReportPage({ params }: { params: Promise<{ token: string }> }) {
  const [data, setData] = useState<ReportPayload | null>(null);
  const [status, setStatus] = useState<"loading" | "missing" | "ready">("loading");
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    setStatus("loading");
    void (async () => {
      try {
        const { token } = await params;
        const response = await fetch(`/api/parent-report/${encodeURIComponent(token)}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("Report unavailable");
        const payload = await response.json() as ReportPayload;
        if (active) { setData(payload); setStatus("ready"); }
      } catch {
        if (active) setStatus("missing");
      }
    })();
    return () => { active = false; controller.abort(); };
  }, [params]);
  if (status === "loading") return <main className="flex min-h-screen items-center justify-center bg-[#f3f7fc]"><RefreshCw className="h-6 w-6 animate-spin text-blue-700" /></main>;
  if (status === "missing" || !data) return <main className="flex min-h-screen items-center justify-center bg-[#f3f7fc] px-5"><section className="max-w-md rounded-3xl bg-white p-8 text-center shadow-sm"><LockKeyhole className="mx-auto h-7 w-7 text-slate-400" /><h1 className="mt-4 text-xl font-black">리포트를 찾을 수 없습니다.</h1><p className="mt-2 text-sm leading-6 text-slate-500">전달받은 주소를 다시 확인해 주세요.</p></section></main>;
  const latest = data.reports[0]?.report || {};
  return <main className="min-h-screen bg-[#f3f7fc] text-slate-900"><header className="border-b border-slate-200 bg-white"><div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-4 sm:px-6"><img src="/dr-yoon-plenus-logo.png" alt="Dr. Yoon Plenus 천안백석캠퍼스" className="h-11 w-auto object-contain sm:h-14" /><span className="flex items-center gap-1.5 text-xs font-bold text-slate-500"><LockKeyhole className="h-3.5 w-3.5" />개인 리포트</span></div></header><div className="mx-auto max-w-4xl px-4 py-7 sm:px-6 sm:py-10"><section className="rounded-3xl bg-[#203568] px-6 py-7 text-white shadow-lg"><p className="text-sm font-semibold text-blue-100">최근 2주 학습 기록</p><h1 className="mt-2 text-2xl font-black">{data.student.displayName} 학생 리포트</h1><p className="mt-2 text-sm text-blue-100">{[data.student.className, data.student.teacherName].filter(Boolean).join(" · ")}</p></section><Tabs defaultValue="record" className="mt-6 space-y-5"><TabsList className="grid h-auto w-full grid-cols-3 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm"><TabsTrigger value="record" className="h-11 rounded-xl text-sm">2주 기록</TabsTrigger><TabsTrigger value="class" className="h-11 rounded-xl text-sm">수업</TabsTrigger><TabsTrigger value="homework" className="h-11 rounded-xl text-sm">숙제</TabsTrigger></TabsList><TabsContent value="record" className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div className="flex items-center gap-2"><CalendarDays className="h-5 w-5 text-blue-700" /><h2 className="text-lg font-black">최근 2주 기록</h2></div><div className="mt-5"><TextList value={latest.records} empty="아직 공개된 2주 기록이 없습니다." /></div></TabsContent><TabsContent value="class" className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div className="flex items-center gap-2"><CalendarDays className="h-5 w-5 text-blue-700" /><h2 className="text-lg font-black">수업 안내</h2></div><div className="mt-5"><TextList value={latest.lessons} empty="공개된 수업 안내가 없습니다." /></div></TabsContent><TabsContent value="homework" className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div className="flex items-center gap-2"><BookOpenCheck className="h-5 w-5 text-blue-700" /><h2 className="text-lg font-black">최근 숙제</h2></div><div className="mt-5"><TextList value={latest.homework} empty="공개된 숙제가 없습니다." /></div></TabsContent></Tabs><footer className="mt-8 text-center text-xs leading-5 text-slate-500">이 리포트는 해당 학생의 보호자에게만 제공됩니다. 링크를 다른 사람에게 전달하지 마세요.</footer></div></main>;
}
