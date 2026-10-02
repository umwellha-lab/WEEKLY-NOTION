# Supabase teacher cache integration

## Purpose

Teacher pages should read the prepared daily payload from Supabase instead of
querying several Notion databases during every page load.

Notion remains the source of truth. GitHub Actions refreshes the cache.

## Supabase project

- Project ref: `axwxxqxmozxluduonzql`
- Region: `ap-northeast-2` (Seoul)
- Table: `public.teacher_daily_cache`
- Access table: `public.teacher_cache_access`
- Ingest Edge Function: `teacher-cache-ingest`
- Read Edge Function: `teacher-cache-read`

## Read request

The read endpoint supports two authorization modes.

### Preferred: site backend

Keep the Supabase secret on the server only.

```http
GET https://axwxxqxmozxluduonzql.supabase.co/functions/v1/teacher-cache-read?teacher=Amber&date=2026-10-01
apikey: <Supabase secret key>
```

### Teacher-scoped browser token

A separate random token exists for each teacher. The plaintext value is stored
in Supabase Vault and only its SHA-256 hash is stored in
`teacher_cache_access`.

```http
GET https://axwxxqxmozxluduonzql.supabase.co/functions/v1/teacher-cache-read?teacher=Amber&date=2026-10-01
x-teacher-token: <Amber-only token>
```

A teacher token is limited to the matching teacher name. Do not reuse one
teacher's token on another teacher page.

Allowed browser origins are `dryoonbs.com`, its subdomains, and
`*.chatgpt.site`.

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

Both cache tables have RLS enabled and no public policies. Direct anonymous
database access is blocked. Browser access must go through the read Edge
Function with a teacher-scoped token. Server access may use a Supabase secret.

## Site migration pattern

1. On teacher-page load, call `teacher-cache-read` first.
2. Render `schedule` and `daily_management` from the returned JSON.
3. Keep current Notion write operations during the initial migration.
4. After a successful write, update the local UI immediately; scheduled sync is
   the fallback.
5. Keep the current Notion read path temporarily as an error fallback until
   production verification is complete.
