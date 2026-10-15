declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    D1_SYNC_TOKEN?: string;
  }
}
