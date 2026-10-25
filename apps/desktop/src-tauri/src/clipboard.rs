//! Clipboard image access for the chat composer.
//!
//! WebKitGTK does not reliably expose image data through the DOM paste event,
//! so the composer falls back to this command when a paste carries neither
//! files nor text. On Linux we read the raw image bytes from the Wayland or X11
//! clipboard and return them as base64 for the existing attachment upload path.

use serde::Serialize;

#[cfg(target_os = "linux")]
use std::process::Command;

#[cfg(target_os = "linux")]
use base64::Engine as _;

/// Refuse images that would bloat the IPC payload and the staged upload.
#[cfg(target_os = "linux")]
const MAX_CLIPBOARD_IMAGE_BYTES: usize = 12 * 1024 * 1024;

/// Clipboard image types we can read, in preference order. Screenshot tools
/// usually publish PNG, but some Wayland compositors only expose JPEG or WebP.
#[cfg(target_os = "linux")]
const IMAGE_TYPES: [&str; 4] = ["image/png", "image/jpeg", "image/webp", "image/gif"];

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClipboardImage {
    pub data_base64: String,
    pub mime: String,
    pub bytes: usize,
}

#[tauri::command]
pub async fn read_clipboard_image() -> Result<Option<ClipboardImage>, String> {
    tauri::async_runtime::spawn_blocking(read_clipboard_image_blocking)
        .await
        .map_err(|error| format!("clipboard task failed: {error}"))?
}

#[cfg(target_os = "linux")]
fn read_clipboard_image_blocking() -> Result<Option<ClipboardImage>, String> {
    if let Some(image) = read_with_wl_paste()? {
        return Ok(Some(image));
    }
    read_with_xclip()
}

#[cfg(not(target_os = "linux"))]
fn read_clipboard_image_blocking() -> Result<Option<ClipboardImage>, String> {
    Err("clipboard image paste is currently supported on Linux only".to_owned())
}

#[cfg(target_os = "linux")]
fn read_with_wl_paste() -> Result<Option<ClipboardImage>, String> {
    let listing = match Command::new("wl-paste").arg("--list-types").output() {
        Ok(output) if output.status.success() => {
            String::from_utf8_lossy(&output.stdout).into_owned()
        }
        // Missing binary, no compositor, or no clipboard owner: try xclip.
        Ok(_) | Err(_) => return Ok(None),
    };
    let Some(mime) = image_type_from_listing(&listing) else {
        return Ok(None);
    };
    let output = Command::new("wl-paste")
        .args(["--type", mime, "--no-newline"])
        .output()
        .map_err(|error| format!("could not run wl-paste: {error}"))?;
    if !output.status.success() {
        return Ok(None);
    }
    finish_image(output.stdout, mime)
}

#[cfg(target_os = "linux")]
fn read_with_xclip() -> Result<Option<ClipboardImage>, String> {
    // xclip is the X11/XWayland fallback. If no selection owner advertises the
    // PNG target it exits non-zero, which simply means there is no image.
    let output = match Command::new("xclip")
        .args(["-selection", "clipboard", "-t", "image/png", "-o"])
        .output()
    {
        Ok(output) => output,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("could not run xclip: {error}")),
    };
    if !output.status.success() {
        return Ok(None);
    }
    finish_image(output.stdout, "image/png")
}

#[cfg(target_os = "linux")]
fn finish_image(bytes: Vec<u8>, mime: &str) -> Result<Option<ClipboardImage>, String> {
    if bytes.is_empty() {
        return Ok(None);
    }
    if bytes.len() > MAX_CLIPBOARD_IMAGE_BYTES {
        return Err(format!(
            "clipboard image is too large ({} MB; limit {} MB)",
            bytes.len() / (1024 * 1024),
            MAX_CLIPBOARD_IMAGE_BYTES / (1024 * 1024)
        ));
    }
    if !has_expected_signature(mime, &bytes) {
        return Ok(None);
    }
    Ok(Some(ClipboardImage {
        data_base64: base64::engine::general_purpose::STANDARD.encode(&bytes),
        mime: mime.to_owned(),
        bytes: bytes.len(),
    }))
}

#[cfg(target_os = "linux")]
fn image_type_from_listing(listing: &str) -> Option<&'static str> {
    let available = listing
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();
    IMAGE_TYPES.into_iter().find(|mime| {
        available
            .iter()
            .any(|candidate| candidate.eq_ignore_ascii_case(mime))
    })
}

#[cfg(target_os = "linux")]
fn has_expected_signature(mime: &str, bytes: &[u8]) -> bool {
    match mime {
        "image/png" => bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg" => bytes.starts_with(&[0xff, 0xd8, 0xff]),
        "image/gif" => bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        "image/webp" => {
            bytes.len() >= 12 && &bytes[0..4] == b"RIFF" && &bytes[8..12] == b"WEBP"
        }
        _ => false,
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;

    #[test]
    fn image_type_listing_prefers_png() {
        let listing = "text/plain\nimage/jpeg\nimage/png\n";
        assert_eq!(image_type_from_listing(listing), Some("image/png"));
        assert_eq!(image_type_from_listing("image/jpeg\n"), Some("image/jpeg"));
        assert_eq!(image_type_from_listing("text/plain\n"), None);
        assert_eq!(image_type_from_listing(""), None);
    }

    #[test]
    fn clipboard_signatures_are_validated() {
        assert!(has_expected_signature("image/png", b"\x89PNG\r\n\x1a\nrest"));
        assert!(!has_expected_signature("image/png", b"not a png"));
        assert!(has_expected_signature("image/jpeg", &[0xff, 0xd8, 0xff, 0x00]));
        assert!(has_expected_signature("image/gif", b"GIF89a..."));
        assert!(has_expected_signature(
            "image/webp",
            b"RIFF\x00\x00\x00\x00WEBPrest"
        ));
    }

    #[test]
    fn clipboard_images_are_base64_encoded_and_size_limited() {
        let png = b"\x89PNG\r\n\x1a\nbody".to_vec();
        let image = finish_image(png.clone(), "image/png")
            .expect("image")
            .expect("some image");
        assert_eq!(image.mime, "image/png");
        assert_eq!(image.bytes, png.len());
        assert_eq!(
            base64::engine::general_purpose::STANDARD
                .decode(image.data_base64)
                .expect("base64"),
            png
        );

        let oversized = vec![0u8; MAX_CLIPBOARD_IMAGE_BYTES + 1];
        assert!(finish_image(oversized, "image/png").is_err());
        assert!(
            finish_image(Vec::new(), "image/png")
                .expect("empty is not an error")
                .is_none()
        );
        assert!(
            finish_image(b"not an image".to_vec(), "image/png")
                .expect("bad signature is not an error")
                .is_none()
        );
    }
}
