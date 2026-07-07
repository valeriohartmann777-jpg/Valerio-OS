/**
 * Optional Supabase client for real cross-device tile saving.
 *
 * Created lazily and ONLY when both env vars are present; otherwise the base
 * stays on localStorage and never touches Supabase. Single-user personal setup:
 * the anon key is public in the browser by design (see the README level-up
 * section), so treat the data as not-secret or add auth later.
 */
import { createClient, type SupabaseClient } from '@supabase/supabase-js'

let client: SupabaseClient | null = null

export function supa(): SupabaseClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  if (!url || !key) return null
  if (!client) client = createClient(url, key)
  return client
}
