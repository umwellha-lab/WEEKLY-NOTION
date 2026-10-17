import { readNotionReports } from "./notion-report";
import { and, eq, gt, isNull, or } from "drizzle-orm";
import { getDb } from "@/db";
import { parentAccess, students } from "@/db/schema";

export type ReportRecord = { weekStart: string; report: Record<string, unknown> };

async function hashToken(token: string) {
  const bytes = new TextEncoder().encode(token);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function getParentReport(token: string) {
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(token)) return null;
  const db = getDb();
  const now = new Date();
  const tokenHash = await hashToken(token);
  const accessRows = await db
    .select({ studentId: parentAccess.studentId })
    .from(parentAccess)
    .where(and(eq(parentAccess.tokenHash, tokenHash), isNull(parentAccess.revokedAt), or(isNull(parentAccess.expiresAt), gt(parentAccess.expiresAt, now))))
    .limit(1);
  const access = accessRows[0];
  if (!access) return null;

  const studentRows = await db
    .select({ notionId: students.notionId, displayName: students.displayName, className: students.className, teacherName: students.teacherName })
    .from(students)
    .where(and(eq(students.id, access.studentId), eq(students.active, true)))
    .limit(1);
  const student = studentRows[0];
  if (!student) return null;

  const reports = await readNotionReports(student.notionId);
  return {
    student: { displayName: student.displayName, className: student.className || "", teacherName: student.teacherName || "" },
    reports,
  };
}
