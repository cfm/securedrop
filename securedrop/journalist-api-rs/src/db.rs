use anyhow::Result;
use blake2::{Blake2s256, Digest};
use chrono::NaiveDateTime;
use chrono::Utc;
use r2d2::Pool;
use r2d2_sqlite::SqliteConnectionManager;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};

pub type DbPool = Pool<SqliteConnectionManager>;

pub fn open_pool(path: &str, busy_timeout_ms: u64) -> Result<DbPool> {
    let manager = SqliteConnectionManager::file(path).with_init(move |conn| {
        conn.execute_batch(&format!("PRAGMA busy_timeout={busy_timeout_ms};"))
    });
    let pool = Pool::builder().max_size(4).build(manager)?;
    Ok(pool)
}

pub fn json_version(m: &BTreeMap<String, Value>) -> String {
    let s = serde_json::to_string(m).expect("BTreeMap serialization is infallible");
    let digest = Blake2s256::digest(s.as_bytes());
    hex::encode(digest)
}

fn is_file(filename: &str) -> bool {
    filename.ends_with("doc.gz.gpg") || filename.ends_with("doc.zip.gpg")
}

fn interaction_count(filename: &str) -> i64 {
    filename
        .split('-')
        .next()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0)
}

fn format_naive_dt(s: &str) -> String {
    if let Ok(dt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f") {
        if dt.and_utc().timestamp_subsec_micros() > 0 {
            format!("{}", dt.format("%Y-%m-%dT%H:%M:%S%.6f"))
        } else {
            format!("{}", dt.format("%Y-%m-%dT%H:%M:%S"))
        }
    } else {
        s.replace(' ', "T")
    }
}

fn format_now_utc() -> String {
    format!("{}", Utc::now().format("%Y-%m-%dT%H:%M:%S%.6f+00:00"))
}

fn parse_group_concat(s: Option<String>) -> Vec<String> {
    match s {
        None => vec![],
        Some(v) if v.is_empty() => vec![],
        Some(v) => v.split(',').map(str::to_string).collect(),
    }
}

struct SourceRow {
    id: i64,
    uuid: String,
    journalist_designation: String,
    last_updated: Option<String>,
    pgp_public_key: Option<String>,
    pgp_fingerprint: Option<String>,
    starred: bool,
}

struct SubmissionRow {
    uuid: String,
    source_id: i64,
    filename: String,
    size: Option<i64>,
    downloaded: bool,
    seen_uuids: Vec<String>,
}

struct ReplyRow {
    uuid: String,
    source_id: i64,
    filename: String,
    size: Option<i64>,
    deleted_by_source: bool,
    journalist_uuid: String,
    seen_uuids: Vec<String>,
}

struct JournalistRow {
    uuid: String,
    username: String,
    first_name: Option<String>,
    last_name: Option<String>,
}

pub fn build_index(pool: &DbPool, minor: u8) -> Result<BTreeMap<String, Value>> {
    let conn = pool.get()?;

    let mut stmt = conn.prepare(
        "SELECT s.id, s.uuid, s.journalist_designation, s.last_updated,
                s.pgp_public_key, s.pgp_fingerprint,
                COALESCE(ss.starred, 0) AS starred
         FROM sources s
         LEFT JOIN source_stars ss ON ss.source_id = s.id
         WHERE s.pending = 0 AND s.deleted_at IS NULL",
    )?;
    let sources: Vec<SourceRow> = stmt
        .query_map([], |row| {
            Ok(SourceRow {
                id: row.get(0)?,
                uuid: row.get(1)?,
                journalist_designation: row.get(2)?,
                last_updated: row.get(3)?,
                pgp_public_key: row.get(4)?,
                pgp_fingerprint: row.get(5)?,
                starred: row.get::<_, i64>(6)? != 0,
            })
        })?
        .collect::<rusqlite::Result<_>>()?;

    let mut stmt = conn.prepare(
        "SELECT sub.uuid, sub.source_id, sub.filename, sub.size, sub.downloaded,
                GROUP_CONCAT(j_sf.uuid) AS seen_file_uuids,
                GROUP_CONCAT(j_sm.uuid) AS seen_msg_uuids
         FROM submissions sub
         JOIN sources src ON src.id = sub.source_id AND src.pending=0 AND src.deleted_at IS NULL
         LEFT JOIN seen_files sf ON sf.file_id = sub.id
         LEFT JOIN journalists j_sf ON j_sf.id = sf.journalist_id
         LEFT JOIN seen_messages sm ON sm.message_id = sub.id
         LEFT JOIN journalists j_sm ON j_sm.id = sm.journalist_id
         GROUP BY sub.id ORDER BY sub.id",
    )?;
    let submissions: Vec<SubmissionRow> = stmt
        .query_map([], |row| {
            let filename: String = row.get(2)?;
            let seen_file_uuids: Option<String> = row.get(5)?;
            let seen_msg_uuids: Option<String> = row.get(6)?;
            let seen_uuids = if is_file(&filename) {
                parse_group_concat(seen_file_uuids)
            } else {
                parse_group_concat(seen_msg_uuids)
            };
            Ok(SubmissionRow {
                uuid: row.get(0)?,
                source_id: row.get(1)?,
                filename,
                size: row.get(3)?,
                downloaded: row.get::<_, i64>(4)? != 0,
                seen_uuids,
            })
        })?
        .collect::<rusqlite::Result<_>>()?;

    let mut stmt = conn.prepare(
        "SELECT r.uuid, r.source_id, r.filename, r.size, r.deleted_by_source,
                j_auth.uuid AS journalist_uuid,
                GROUP_CONCAT(j_sr.uuid) AS seen_reply_uuids
         FROM replies r
         JOIN sources src ON src.id = r.source_id AND src.pending=0 AND src.deleted_at IS NULL
         JOIN journalists j_auth ON j_auth.id = r.journalist_id
         LEFT JOIN seen_replies sr ON sr.reply_id = r.id
         LEFT JOIN journalists j_sr ON j_sr.id = sr.journalist_id
         GROUP BY r.id ORDER BY r.id",
    )?;
    let replies: Vec<ReplyRow> = stmt
        .query_map([], |row| {
            let seen_uuids: Option<String> = row.get(6)?;
            Ok(ReplyRow {
                uuid: row.get(0)?,
                source_id: row.get(1)?,
                filename: row.get(2)?,
                size: row.get(3)?,
                deleted_by_source: row.get::<_, i64>(4)? != 0,
                journalist_uuid: row.get(5)?,
                seen_uuids: parse_group_concat(seen_uuids),
            })
        })?
        .collect::<rusqlite::Result<_>>()?;

    let mut stmt = conn.prepare(
        "SELECT uuid, username, first_name, last_name FROM journalists",
    )?;
    let journalists: Vec<JournalistRow> = stmt
        .query_map([], |row| {
            Ok(JournalistRow {
                uuid: row.get(0)?,
                username: row.get(1)?,
                first_name: row.get(2)?,
                last_name: row.get(3)?,
            })
        })?
        .collect::<rusqlite::Result<_>>()?;

    let mut subs_by_source: HashMap<i64, Vec<&SubmissionRow>> = HashMap::new();
    for sub in &submissions {
        subs_by_source.entry(sub.source_id).or_default().push(sub);
    }

    let source_uuid_by_id: HashMap<i64, &str> =
        sources.iter().map(|s| (s.id, s.uuid.as_str())).collect();

    let mut index_sources: BTreeMap<String, Value> = BTreeMap::new();
    let mut index_items: BTreeMap<String, Value> = BTreeMap::new();
    let mut index_journalists: BTreeMap<String, Value> = BTreeMap::new();

    for source in &sources {
        let source_subs = subs_by_source
            .get(&source.id)
            .map(|v| v.as_slice())
            .unwrap_or(&[]);

        let has_attachment = source_subs.iter().any(|sub| is_file(&sub.filename));
        let is_seen = source_subs.is_empty()
            || source_subs
                .iter()
                .all(|sub| sub.downloaded || !sub.seen_uuids.is_empty());

        let last_updated = match &source.last_updated {
            Some(s) => format_naive_dt(s),
            None => format_now_utc(),
        };

        let mut m: BTreeMap<String, Value> = BTreeMap::new();
        m.insert(
            "fingerprint".into(),
            match &source.pgp_fingerprint {
                Some(f) => Value::String(f.clone()),
                None => Value::Null,
            },
        );
        m.insert("has_attachment".into(), json!(has_attachment));
        m.insert("is_seen".into(), json!(is_seen));
        m.insert("is_starred".into(), json!(source.starred));
        m.insert(
            "journalist_designation".into(),
            json!(source.journalist_designation),
        );
        m.insert("last_updated".into(), Value::String(last_updated));
        m.insert(
            "public_key".into(),
            match &source.pgp_public_key {
                Some(k) => Value::String(k.clone()),
                None => Value::Null,
            },
        );
        m.insert("uuid".into(), json!(source.uuid));

        index_sources.insert(source.uuid.clone(), Value::String(json_version(&m)));
    }

    for sub in &submissions {
        let source_uuid = match source_uuid_by_id.get(&sub.source_id) {
            Some(u) => *u,
            None => continue,
        };

        let kind = if is_file(&sub.filename) { "file" } else { "message" };
        let is_read = sub.downloaded || !sub.seen_uuids.is_empty();

        let mut m: BTreeMap<String, Value> = BTreeMap::new();
        if minor >= 2 {
            m.insert(
                "interaction_count".into(),
                json!(interaction_count(&sub.filename)),
            );
        }
        m.insert("is_read".into(), json!(is_read));
        m.insert("kind".into(), json!(kind));
        m.insert("seen_by".into(), json!(sub.seen_uuids));
        m.insert("size".into(), json!(sub.size.unwrap_or(0)));
        m.insert("source".into(), json!(source_uuid));
        m.insert("uuid".into(), json!(sub.uuid));

        index_items.insert(sub.uuid.clone(), Value::String(json_version(&m)));
    }

    for reply in &replies {
        let source_uuid = match source_uuid_by_id.get(&reply.source_id) {
            Some(u) => *u,
            None => continue,
        };

        let mut m: BTreeMap<String, Value> = BTreeMap::new();
        if minor >= 2 {
            m.insert(
                "interaction_count".into(),
                json!(interaction_count(&reply.filename)),
            );
        }
        m.insert("is_deleted_by_source".into(), json!(reply.deleted_by_source));
        m.insert("journalist_uuid".into(), json!(reply.journalist_uuid));
        m.insert("kind".into(), json!("reply"));
        m.insert("seen_by".into(), json!(reply.seen_uuids));
        m.insert("size".into(), json!(reply.size.unwrap_or(0)));
        m.insert("source".into(), json!(source_uuid));
        m.insert("uuid".into(), json!(reply.uuid));

        index_items.insert(reply.uuid.clone(), Value::String(json_version(&m)));
    }

    for journalist in &journalists {
        let mut m: BTreeMap<String, Value> = BTreeMap::new();
        m.insert(
            "first_name".into(),
            match &journalist.first_name {
                Some(f) => Value::String(f.clone()),
                None => Value::Null,
            },
        );
        m.insert(
            "last_name".into(),
            match &journalist.last_name {
                Some(l) => Value::String(l.clone()),
                None => Value::Null,
            },
        );
        m.insert("username".into(), json!(journalist.username));
        m.insert("uuid".into(), json!(journalist.uuid));

        index_journalists.insert(journalist.uuid.clone(), Value::String(json_version(&m)));
    }

    let mut index: BTreeMap<String, Value> = BTreeMap::new();
    index.insert(
        "items".into(),
        serde_json::to_value(&index_items).expect("BTreeMap serialization is infallible"),
    );
    index.insert(
        "journalists".into(),
        serde_json::to_value(&index_journalists).expect("BTreeMap serialization is infallible"),
    );
    index.insert(
        "sources".into(),
        serde_json::to_value(&index_sources).expect("BTreeMap serialization is infallible"),
    );

    if minor < 1 {
        index.remove("journalists");
    }

    Ok(index)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_json_version_golden() {
        // Must match test_journalist_api2.py:59
        let mut m: BTreeMap<String, Value> = BTreeMap::new();
        m.insert("baz".into(), json!("biz"));
        m.insert("foo".into(), json!("bar"));
        let s = serde_json::to_string(&m).unwrap();
        assert_eq!(s, r#"{"baz":"biz","foo":"bar"}"#);
        let h = hex::encode(Blake2s256::digest(s.as_bytes()));
        assert_eq!(
            h,
            "2231968214a50f92d216048c7fc624c061372a4225e9e94aca88bdfaca162087"
        );
    }

    #[test]
    fn test_datetime_naive_no_micros() {
        assert_eq!(format_naive_dt("2024-01-15 10:30:00"), "2024-01-15T10:30:00");
    }

    #[test]
    fn test_datetime_naive_with_micros() {
        assert_eq!(
            format_naive_dt("2024-01-15 10:30:00.123456"),
            "2024-01-15T10:30:00.123456"
        );
    }
}
