// Teacher-page cache client for dryoonbs.com.
// The caller provides a teacher-scoped token. The token can only read one
// teacher's cache through the Supabase Edge Function.

const TEACHER_CACHE_ENDPOINT =
  "https://axwxxqxmozxluduonzql.supabase.co/functions/v1/teacher-cache-read";

export async function fetchTeacherCache({ teacher, token, date }) {
  if (!teacher || !token) throw new Error("teacher and token are required");

  const url = new URL(TEACHER_CACHE_ENDPOINT);
  url.searchParams.set("teacher", teacher);
  if (date) url.searchParams.set("date", date);

  const res = await fetch(url, {
    method: "GET",
    headers: { "x-teacher-token": token },
  });

  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`teacher cache read failed: ${res.status} ${body}`);
  }

  const body = await res.json();
  return body?.cache ?? null;
}

export function splitTeacherCache(cache) {
  return {
    date: cache?.date ?? null,
    schedule: Array.isArray(cache?.data?.schedule) ? cache.data.schedule : [],
    dailyManagement: Array.isArray(cache?.data?.daily_management)
      ? cache.data.daily_management
      : [],
    updatedAt: cache?.updated_at ?? null,
  };
}
