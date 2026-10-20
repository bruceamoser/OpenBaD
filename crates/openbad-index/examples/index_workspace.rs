//! Refresh and report the workspace index.
//!
//! ```bash
//! cargo run -p openbad-index --example index_workspace -- . /tmp/openbad-index.db
//! ```

use std::path::PathBuf;

use openbad_index::WorkspaceIndexer;
use openbad_store::Store;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args().skip(1);
    let root = PathBuf::from(arguments.next().unwrap_or_else(|| ".".to_owned()));
    let store = match arguments.next() {
        Some(path) => Store::open(path)?,
        None => Store::open_default()?,
    };
    let name = root
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("workspace");
    let workspace = store.ensure_workspace(name, &root.to_string_lossy())?;
    let report = WorkspaceIndexer::new(&store, workspace, &root).refresh()?;
    println!(
        "seen={} added={} updated={} removed={} elapsed_ms={} files={} symbols={} bytes={}",
        report.seen,
        report.added,
        report.updated,
        report.removed,
        report.elapsed_ms,
        report.summary.files,
        report.summary.symbols,
        report.summary.bytes
    );
    if let Some(query) = arguments.next() {
        for hit in store.search_workspace(workspace, &query, 5)? {
            println!("{}: {}", hit.path, hit.snippet.replace('\n', " "));
        }
    }
    Ok(())
}
