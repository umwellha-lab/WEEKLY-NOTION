# Supabase teacher cache integration

## Purpose

Teacher pages should read the prepared daily payload from Supabase instead of
querying several Notion databases during every page load.

Notion remains the source of truth. GitHub Actions refreshes the cache.

## Supabase project

- Project ref: `axwxxqxmozxluduonzql`
- Region: `ap-northeast-2` (Seoul)
- Table: `public.teacher_daily_cache`
- Ingest Edge Function: `teacher-cache-ingest`
- Read Edge Function: `teacher-cache-read`

Do not place a Supabase secret/service-role key in browser JavaScript.

## Read request

Call from the site's server/backend only:

```http
GET https://axwxxqxmozxluduonzql.supabase.co/functions/v1/teacher-cache-read?teacher=Amber&date=2026-10-01
apikey: <Supabase secret key>
```

If `date` is omitted, the newest cached date for that teacher is returned.

## Cache refresh

`.github/workflows/sync_teacher_cache.yml`

- Weekdays: every 15 minutes during KST 13:00-23:59.
- Weekends: every 30 minutes during KST 09:00-18:59.
- Uses the existing GitHub `NOTION_TOKEN`.
- GitHub authenticates to the Supabase ingest function through GitHub OIDC.
- No Supabase secret is stored in GitHub.

The normal daily-management workflow also refreshes the cache after its Notion
generation work.

## Security

`teacher_daily_cache` has RLS enabled and no public read policy. Direct
anonymous access is intentionally blocked. The read Edge Function accepts only
a Supabase server secret/service-role key.

## Site migration pattern

1. On teacher-page load, call `teacher-cache-read` first.
2. Render `schedule` and `daily_management` from the returned JSON.
3. Keep current Notion write operations during the initial migration.
4. After a successful write, refresh the local UI immediately; scheduled sync is the fallback.
5. Keep the current Notion read path temporarily as an error fallback until production verification is complete.
