//! File previews for the desktop preview pane.
//!
//! Classifies a workspace file and returns a payload the UI can render:
//! inline text, images and media as bounded data URLs, spreadsheet sheets as
//! tables, or extracted text for PDF/Word/PowerPoint documents. Unknown binary
//! formats fall back to a size summary so the UI can offer "open externally"
//! instead of a UTF-8 error.

use std::io::{BufRead, BufReader};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::Path;

use base64::Engine as _;
use calamine::{Data, Reader, open_workbook_auto};
use serde::Serialize;

/// Largest text preview we accept, matching the previous preview limit.
pub const MAX_TEXT_BYTES: u64 = 8 * 1024 * 1024;
/// Images are embedded as data URLs; keep the payload bounded.
pub const MAX_IMAGE_BYTES: u64 = 16 * 1024 * 1024;
/// Audio and video are embedded as data URLs; keep the payload bounded.
pub const MAX_MEDIA_BYTES: u64 = 32 * 1024 * 1024;
/// PDF, Word, PowerPoint, and spreadsheet parsing limit.
pub const MAX_DOCUMENT_BYTES: u64 = 32 * 1024 * 1024;
const MAX_TEXT_PREVIEW_CHARS: usize = 400_000;
const MAX_SHEET_ROWS: usize = 200;
const MAX_SHEET_COLUMNS: usize = 40;
const MAX_SHEETS: usize = 8;

#[derive(Debug, Clone, Serialize)]
pub struct SheetPreview {
    pub name: String,
    pub rows: Vec<Vec<String>>,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProjectFileView {
    pub path: String,
    /// `text`, `image`, `pdf`, `sheet`, `doc`, `audio`, `video`, or `binary`.
    pub kind: String,
    pub content: Option<String>,
    pub data_url: Option<String>,
    pub mime: Option<String>,
    pub size: u64,
    pub sheets: Vec<SheetPreview>,
    pub truncated: bool,
}

impl ProjectFileView {
    fn new(path: String, kind: &str, size: u64) -> Self {
        Self {
            path,
            kind: kind.to_owned(),
            content: None,
            data_url: None,
            mime: None,
            size,
            sheets: Vec::new(),
            truncated: false,
        }
    }
}

/// Extension-only classification. Tests pin the mapping so adding a type is a
/// deliberate change rather than a side effect.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PreviewKind {
    Text,
    Image,
    Pdf,
    Sheet,
    Doc,
    Audio,
    Video,
    Binary,
}

#[must_use]
pub fn classify(extension: &str) -> PreviewKind {
    match extension {
        "png" | "jpg" | "jpeg" | "gif" | "webp" | "bmp" | "ico" | "svg" | "avif" | "apng" => {
            PreviewKind::Image
        }
        "pdf" => PreviewKind::Pdf,
        "csv" | "tsv" | "xls" | "xlsx" | "xlsm" | "ods" => PreviewKind::Sheet,
        "docx" | "pptx" => PreviewKind::Doc,
        "mp3" | "wav" | "ogg" | "oga" | "m4a" | "aac" | "flac" | "opus" => PreviewKind::Audio,
        "mp4" | "webm" | "ogv" | "mov" | "m4v" => PreviewKind::Video,
        // Markup and source files are text even when the preview pane is not
        // rendering them as Markdown.
        "md" | "markdown" | "mdx" | "txt" | "log" | "json" | "jsonl" | "yaml" | "yml" | "toml"
        | "xml" | "html" | "htm" | "css" | "scss" | "js" | "jsx" | "ts" | "tsx" | "rs" | "py"
        | "go" | "java" | "kt" | "rb" | "php" | "c" | "h" | "cc" | "cpp" | "hpp" | "cs" | "sh"
        | "bash" | "zsh" | "ps1" | "sql" | "ini" | "cfg" | "conf" | "env" | "gitignore"
        | "dockerfile" | "makefile" | "gradle" | "lua" | "swift" | "scala" | "ex" | "exs"
        | "erl" | "hs" | "clj" | "vim" | "rst" | "tex" | "diff" | "patch" => PreviewKind::Text,
        _ => PreviewKind::Binary,
    }
}

fn mime_for(extension: &str) -> &'static str {
    match extension {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "ico" => "image/x-icon",
        "svg" => "image/svg+xml",
        "avif" => "image/avif",
        "apng" => "image/apng",
        "pdf" => "application/pdf",
        "mp3" => "audio/mpeg",
        "wav" => "audio/wav",
        "ogg" | "oga" => "audio/ogg",
        "m4a" => "audio/mp4",
        "aac" => "audio/aac",
        "flac" => "audio/flac",
        "opus" => "audio/opus",
        "mp4" | "m4v" => "video/mp4",
        "webm" => "video/webm",
        "ogv" => "video/ogg",
        "mov" => "video/quicktime",
        _ => "application/octet-stream",
    }
}

/// Builds the preview payload for an already-resolved file.
pub fn preview_file(resolved: &Path, relative: String) -> Result<ProjectFileView, String> {
    let metadata = std::fs::metadata(resolved).map_err(|error| error.to_string())?;
    if !metadata.is_file() {
        return Err("preview path is not a file".to_owned());
    }
    let size = metadata.len();
    let extension = resolved
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default()
        .to_lowercase();
    let kind = classify(&extension);
    match kind {
        PreviewKind::Image => {
            let bytes = read_bounded(resolved, size, MAX_IMAGE_BYTES, "image")?;
            Ok(data_url_view(
                relative,
                "image",
                size,
                mime_for(&extension),
                &bytes,
            ))
        }
        PreviewKind::Audio | PreviewKind::Video => {
            let bytes = read_bounded(resolved, size, MAX_MEDIA_BYTES, "media")?;
            let kind = if kind == PreviewKind::Audio {
                "audio"
            } else {
                "video"
            };
            Ok(data_url_view(
                relative,
                kind,
                size,
                mime_for(&extension),
                &bytes,
            ))
        }
        PreviewKind::Pdf => {
            check_size(size, MAX_DOCUMENT_BYTES, "PDF")?;
            let text = guarded(move || {
                pdf_extract::extract_text(resolved).map_err(|error| error.to_string())
            })??;
            let mut view = ProjectFileView::new(relative, "pdf", size);
            let clipped = clip(&text);
            view.truncated = clipped.len() < text.len();
            view.content = Some(clipped);
            Ok(view)
        }
        PreviewKind::Sheet => sheet_view(resolved, relative, size, &extension),
        PreviewKind::Doc => {
            check_size(size, MAX_DOCUMENT_BYTES, "document")?;
            let text = if extension == "docx" {
                guarded(move || docx_text(resolved))?
            } else {
                guarded(move || pptx_text(resolved))?
            }?;
            let mut view = ProjectFileView::new(relative, "doc", size);
            let clipped = clip(&text);
            view.truncated = clipped.len() < text.len();
            view.content = Some(clipped);
            Ok(view)
        }
        PreviewKind::Text | PreviewKind::Binary => {
            if size <= MAX_TEXT_BYTES {
                if let Ok(text) = std::fs::read_to_string(resolved) {
                    let mut view = ProjectFileView::new(relative, "text", size);
                    let clipped = clip(&text);
                    view.truncated = clipped.len() < text.len();
                    view.content = Some(clipped);
                    return Ok(view);
                }
            }
            Ok(ProjectFileView::new(relative, "binary", size))
        }
    }
}

fn sheet_view(
    resolved: &Path,
    relative: String,
    size: u64,
    extension: &str,
) -> Result<ProjectFileView, String> {
    if matches!(extension, "csv" | "tsv") {
        check_size(size, MAX_TEXT_BYTES, "spreadsheet")?;
        let text = std::fs::read_to_string(resolved).map_err(|error| error.to_string())?;
        let delimiter = if extension == "tsv" { '\t' } else { ',' };
        let (rows, truncated) = parse_delimited(&text, delimiter);
        let mut view = ProjectFileView::new(relative, "sheet", size);
        view.truncated = truncated;
        view.sheets = vec![SheetPreview {
            name: extension.to_uppercase(),
            rows,
            truncated,
        }];
        return Ok(view);
    }
    check_size(size, MAX_DOCUMENT_BYTES, "spreadsheet")?;
    let sheets = guarded(move || workbook_sheets(resolved))??;
    let truncated = sheets.iter().any(|sheet| sheet.truncated);
    let mut view = ProjectFileView::new(relative, "sheet", size);
    view.truncated = truncated;
    view.sheets = sheets;
    Ok(view)
}

fn data_url_view(path: String, kind: &str, size: u64, mime: &str, bytes: &[u8]) -> ProjectFileView {
    let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
    let mut view = ProjectFileView::new(path, kind, size);
    view.mime = Some(mime.to_owned());
    view.data_url = Some(format!("data:{mime};base64,{encoded}"));
    view
}

fn read_bounded(resolved: &Path, size: u64, limit: u64, label: &str) -> Result<Vec<u8>, String> {
    check_size(size, limit, label)?;
    std::fs::read(resolved).map_err(|error| error.to_string())
}

fn check_size(size: u64, limit: u64, label: &str) -> Result<(), String> {
    if size > limit {
        return Err(format!(
            "this {label} file is {size} bytes; inline previews are limited to {limit} bytes"
        ));
    }
    Ok(())
}

fn clip(text: &str) -> String {
    text.chars().take(MAX_TEXT_PREVIEW_CHARS).collect()
}

/// Runs a parser inside `catch_unwind` so malformed input surfaces as an error
/// instead of unwinding the command.
fn guarded<T>(
    parse: impl FnOnce() -> Result<T, String> + std::panic::UnwindSafe,
) -> Result<Result<T, String>, String> {
    catch_unwind(AssertUnwindSafe(parse))
        .map_err(|_| "this file could not be parsed for preview".to_owned())
}

/// Parses delimited text with RFC 4180 quoting and bounds the result.
#[must_use]
pub fn parse_delimited(text: &str, delimiter: char) -> (Vec<Vec<String>>, bool) {
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut field = String::new();
    let mut quoted = false;
    let mut chars = text.chars().peekable();
    let mut truncated = false;
    while let Some(character) = chars.next() {
        if quoted {
            if character == '"' {
                if chars.peek() == Some(&'"') {
                    chars.next();
                    field.push('"');
                } else {
                    quoted = false;
                }
            } else {
                field.push(character);
            }
            continue;
        }
        match character {
            '"' if field.is_empty() => quoted = true,
            character if character == delimiter => row.push(std::mem::take(&mut field)),
            '\r' => {}
            '\n' => {
                row.push(std::mem::take(&mut field));
                push_row(&mut rows, std::mem::take(&mut row), &mut truncated);
                if rows.len() >= MAX_SHEET_ROWS {
                    return (rows, true);
                }
            }
            other => field.push(other),
        }
    }
    if !field.is_empty() || !row.is_empty() {
        row.push(field);
        push_row(&mut rows, row, &mut truncated);
    }
    (rows, truncated)
}

fn push_row(rows: &mut Vec<Vec<String>>, mut row: Vec<String>, truncated: &mut bool) {
    if row.len() > MAX_SHEET_COLUMNS {
        row.truncate(MAX_SHEET_COLUMNS);
        *truncated = true;
    }
    while row.last().is_some_and(String::is_empty) {
        row.pop();
    }
    if !row.is_empty() {
        rows.push(row);
    }
}

fn workbook_sheets(path: &Path) -> Result<Vec<SheetPreview>, String> {
    let mut workbook = open_workbook_auto(path).map_err(|error| error.to_string())?;
    let names = workbook.sheet_names().to_vec();
    let mut sheets = Vec::new();
    for name in names.into_iter().take(MAX_SHEETS) {
        let range = workbook
            .worksheet_range(&name)
            .map_err(|error| error.to_string())?;
        let mut rows = Vec::new();
        let mut truncated = false;
        for (row_index, row) in range.rows().enumerate() {
            if row_index >= MAX_SHEET_ROWS {
                truncated = true;
                break;
            }
            let mut cells = row
                .iter()
                .take(MAX_SHEET_COLUMNS)
                .map(cell_text)
                .collect::<Vec<_>>();
            if row.len() > MAX_SHEET_COLUMNS {
                truncated = true;
            }
            while cells.last().is_some_and(String::is_empty) {
                cells.pop();
            }
            rows.push(cells);
        }
        sheets.push(SheetPreview {
            name,
            rows,
            truncated,
        });
    }
    if workbook.sheet_names().len() > MAX_SHEETS {
        if let Some(last) = sheets.last_mut() {
            last.truncated = true;
        }
    }
    Ok(sheets)
}

fn cell_text(cell: &Data) -> String {
    match cell {
        Data::Empty => String::new(),
        Data::Int(value) => value.to_string(),
        Data::Float(value) => {
            if value.fract() == 0.0 {
                format!("{value:.0}")
            } else {
                value.to_string()
            }
        }
        Data::String(value) => value.clone(),
        Data::Bool(value) => value.to_string(),
        Data::DateTime(value) => value.to_string(),
        Data::DateTimeIso(value) | Data::DurationIso(value) => value.clone(),
        Data::Error(error) => format!("#{error:?}"),
    }
}

fn docx_text(path: &Path) -> Result<String, String> {
    let file = std::fs::File::open(path).map_err(|error| error.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|error| error.to_string())?;
    let document = archive
        .by_name("word/document.xml")
        .map_err(|error| format!("this .docx file has no document body: {error}"))?;
    xml_text(BufReader::new(document), b"w:t", b"w:p")
}

fn pptx_text(path: &Path) -> Result<String, String> {
    let file = std::fs::File::open(path).map_err(|error| error.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|error| error.to_string())?;
    let mut slides = archive
        .file_names()
        .filter(|name| name.starts_with("ppt/slides/slide") && name.ends_with(".xml"))
        .map(str::to_owned)
        .collect::<Vec<_>>();
    slides.sort_by_key(|name| {
        name.trim_start_matches("ppt/slides/slide")
            .trim_end_matches(".xml")
            .parse::<u32>()
            .unwrap_or(u32::MAX)
    });
    if slides.is_empty() {
        return Err("this .pptx file has no slides".to_owned());
    }
    let mut text = String::new();
    for (index, name) in slides.iter().enumerate() {
        let slide = archive.by_name(name).map_err(|error| error.to_string())?;
        text.push_str(&format!("\n\n--- Slide {} ---\n\n", index + 1));
        text.push_str(&xml_text(BufReader::new(slide), b"a:t", b"a:p")?);
    }
    Ok(text)
}

/// Extracts text from the OOXML `text_tag` elements, breaking lines at
/// `paragraph_tag` ends.
fn xml_text<R: BufRead>(
    reader: R,
    text_tag: &[u8],
    paragraph_tag: &[u8],
) -> Result<String, String> {
    let mut reader = quick_xml::Reader::from_reader(reader);
    reader.config_mut().trim_text(false);
    let mut output = String::new();
    let mut in_text = false;
    let mut buffer = Vec::new();
    loop {
        match reader.read_event_into(&mut buffer) {
            Ok(quick_xml::events::Event::Start(start)) => {
                if start.name().as_ref() == text_tag {
                    in_text = true;
                }
            }
            Ok(quick_xml::events::Event::End(end)) => {
                if end.name().as_ref() == text_tag {
                    in_text = false;
                } else if end.name().as_ref() == paragraph_tag {
                    output.push('\n');
                }
            }
            Ok(quick_xml::events::Event::Text(text)) => {
                if in_text {
                    let decoded = text.decode().map_err(|error| error.to_string())?;
                    output.push_str(&decoded);
                }
            }
            Ok(quick_xml::events::Event::GeneralRef(reference)) => {
                if in_text {
                    let name = reference.decode().map_err(|error| error.to_string())?;
                    let value = reference
                        .resolve_char_ref()
                        .map_err(|error| error.to_string())?
                        .or_else(|| match name.as_ref() {
                            "amp" => Some('&'),
                            "lt" => Some('<'),
                            "gt" => Some('>'),
                            "quot" => Some('"'),
                            "apos" => Some('\''),
                            _ => None,
                        });
                    if let Some(value) = value {
                        output.push(value);
                    }
                }
            }
            Ok(quick_xml::events::Event::Eof) => break,
            Err(error) => return Err(error.to_string()),
            _ => {}
        }
        buffer.clear();
    }
    Ok(output.trim().to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::tempdir;

    #[test]
    fn extensions_are_classified() {
        assert_eq!(classify("png"), PreviewKind::Image);
        assert_eq!(classify("svg"), PreviewKind::Image);
        assert_eq!(classify("pdf"), PreviewKind::Pdf);
        assert_eq!(classify("xlsx"), PreviewKind::Sheet);
        assert_eq!(classify("csv"), PreviewKind::Sheet);
        assert_eq!(classify("docx"), PreviewKind::Doc);
        assert_eq!(classify("mp3"), PreviewKind::Audio);
        assert_eq!(classify("mp4"), PreviewKind::Video);
        assert_eq!(classify("rs"), PreviewKind::Text);
        assert_eq!(classify("bin"), PreviewKind::Binary);
    }

    #[test]
    fn delimited_text_handles_quotes_and_bounds() {
        let (rows, truncated) = parse_delimited("a,b\n\"x,y\",2\n", ',');
        assert_eq!(rows, vec![vec!["a", "b"], vec!["x,y", "2"]]);
        assert!(!truncated);

        let wide = "a,b,c\n1,2,3";
        let (rows, _) = parse_delimited(wide, ',');
        assert_eq!(rows[1][2], "3");

        let many = (0..MAX_SHEET_ROWS + 20)
            .map(|index| format!("{index},value"))
            .collect::<Vec<_>>()
            .join("\n");
        let (rows, truncated) = parse_delimited(&many, ',');
        assert_eq!(rows.len(), MAX_SHEET_ROWS);
        assert!(truncated);
    }

    #[test]
    fn images_render_as_data_urls() {
        let root = tempdir().expect("tempdir");
        let path = root.path().join("preview.png");
        std::fs::write(&path, [0x89, b'P', b'N', b'G']).expect("png");
        let view = preview_file(&path, "preview.png".to_owned()).expect("preview");
        assert_eq!(view.kind, "image");
        assert_eq!(view.mime.as_deref(), Some("image/png"));
        assert!(
            view.data_url
                .expect("data url")
                .starts_with("data:image/png;base64,")
        );
    }

    #[test]
    fn csv_files_render_as_sheets() {
        let root = tempdir().expect("tempdir");
        let path = root.path().join("data.csv");
        std::fs::write(&path, "name,count\nalpha,1\n").expect("csv");
        let view = preview_file(&path, "data.csv".to_owned()).expect("preview");
        assert_eq!(view.kind, "sheet");
        assert_eq!(view.sheets.len(), 1);
        assert_eq!(view.sheets[0].rows[0], vec!["name", "count"]);
    }

    #[test]
    fn xlsx_files_render_every_sheet() {
        let root = tempdir().expect("tempdir");
        let path = root.path().join("book.xlsx");
        write_xlsx(&path);
        let view = preview_file(&path, "book.xlsx".to_owned()).expect("preview");
        assert_eq!(view.kind, "sheet");
        assert_eq!(view.sheets[0].name, "Sheet1");
        assert_eq!(view.sheets[0].rows[0], vec!["Name", "Count"]);
        assert_eq!(view.sheets[0].rows[1], vec!["alpha", "2"]);
    }

    #[test]
    fn docx_files_extract_paragraph_text() {
        let root = tempdir().expect("tempdir");
        let path = root.path().join("notes.docx");
        write_zip(
            &path,
            &[(
                "word/document.xml",
                r#"<?xml version="1.0"?><w:document xmlns:w="w"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p><w:p><w:r><w:t>World &amp; friends</w:t></w:r></w:p></w:body></w:document>"#,
            )],
        );
        let view = preview_file(&path, "notes.docx".to_owned()).expect("preview");
        assert_eq!(view.kind, "doc");
        let content = view.content.expect("content");
        assert!(content.contains("Hello\n"));
        assert!(content.contains("World & friends"), "content={content:?}");
    }

    #[test]
    fn unknown_binary_files_fall_back_instead_of_failing() {
        let root = tempdir().expect("tempdir");
        let path = root.path().join("payload.bin");
        std::fs::write(&path, [0x00, 0xff, 0x00]).expect("bin");
        let view = preview_file(&path, "payload.bin".to_owned()).expect("preview");
        assert_eq!(view.kind, "binary");
        assert_eq!(view.size, 3);
        assert!(view.content.is_none());
    }

    fn write_zip(path: &Path, entries: &[(&str, &str)]) {
        let file = std::fs::File::create(path).expect("zip file");
        let mut zip = zip::ZipWriter::new(file);
        let options = zip::write::SimpleFileOptions::default();
        for (name, content) in entries {
            zip.start_file(*name, options).expect("start file");
            zip.write_all(content.as_bytes()).expect("write entry");
        }
        zip.finish().expect("finish zip");
    }

    fn write_xlsx(path: &Path) {
        // Minimal single-sheet workbook: header row plus one data row.
        let shared = r#"<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3"><si><t>Name</t></si><si><t>Count</t></si><si><t>alpha</t></si></sst>"#;
        write_zip(
            path,
            &[
                (
                    "[Content_Types].xml",
                    r#"<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>"#,
                ),
                (
                    "_rels/.rels",
                    r#"<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>"#,
                ),
                (
                    "xl/workbook.xml",
                    r#"<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>"#,
                ),
                (
                    "xl/_rels/workbook.xml.rels",
                    r#"<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>"#,
                ),
                (
                    "xl/worksheets/sheet1.xml",
                    r#"<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>2</v></c></row></sheetData></worksheet>"#,
                ),
                ("xl/sharedStrings.xml", shared),
            ],
        );
    }
}
