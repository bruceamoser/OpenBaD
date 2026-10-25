//! Clipboard image access for the chat composer.
//!
//! WebKitGTK does not reliably expose image data through the DOM paste event,
//! so the composer falls back to this command when a paste carries neither
//! files nor text. On Linux we read the clipboard in-process with `arboard`
//! (Wayland data-control when the compositor supports it, X11 otherwise),
//! encode the decoded RGBA pixels as PNG, and return base64 for the existing
//! attachment upload path. No external clipboard tools are required.

use serde::Serialize;

#[cfg(target_os = "linux")]
use base64::Engine as _;
#[cfg(target_os = "linux")]
use png::{BitDepth, ColorType, Encoder};

/// Refuse images that would bloat the IPC payload and the staged upload.
#[cfg(target_os = "linux")]
const MAX_CLIPBOARD_IMAGE_BYTES: usize = 12 * 1024 * 1024;

/// Base64 emits four characters for every three bytes, so the encoded IPC
/// payload has a separate cap derived from the PNG cap.
#[cfg(target_os = "linux")]
const MAX_CLIPBOARD_IMAGE_BASE64_BYTES: usize = MAX_CLIPBOARD_IMAGE_BYTES.div_ceil(3) * 4;

/// Guard against pathological clipboard dimensions before encoding. 16 Mi
/// pixels is 64 MiB of RGBA and still covers 5K screenshots.
#[cfg(target_os = "linux")]
const MAX_CLIPBOARD_IMAGE_PIXELS: usize = 16 * 1024 * 1024;

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
    let mut clipboard = arboard::Clipboard::new()
        .map_err(|error| format!("could not open the system clipboard: {error}"))?;
    let image = match clipboard.get_image() {
        Ok(image) => image,
        // An empty clipboard, text-only contents, or a format arboard cannot
        // decode all mean "no image"; the composer already renders a clear
        // non-blocking notice for `None`.
        Err(arboard::Error::ContentNotAvailable) => return Ok(None),
        Err(error) => {
            return Err(format!(
                "could not read an image from the system clipboard: {error}"
            ));
        }
    };
    finish_image(image)
}

#[cfg(not(target_os = "linux"))]
fn read_clipboard_image_blocking() -> Result<Option<ClipboardImage>, String> {
    Err("clipboard image paste is currently supported on Linux only".to_owned())
}

#[cfg(target_os = "linux")]
fn finish_image(image: arboard::ImageData<'_>) -> Result<Option<ClipboardImage>, String> {
    if image.width == 0 || image.height == 0 {
        return Ok(None);
    }
    let pixels = image
        .width
        .checked_mul(image.height)
        .ok_or_else(|| "clipboard image dimensions are too large".to_owned())?;
    if pixels > MAX_CLIPBOARD_IMAGE_PIXELS {
        return Err(format!(
            "clipboard image is too large ({} MP; limit {} MP)",
            pixels / (1024 * 1024),
            MAX_CLIPBOARD_IMAGE_PIXELS / (1024 * 1024)
        ));
    }
    let expected = pixels
        .checked_mul(4)
        .ok_or_else(|| "clipboard image dimensions are too large".to_owned())?;
    if image.bytes.len() != expected {
        return Err("the clipboard image data is malformed".to_owned());
    }
    let png = encode_rgba_png(image.width, image.height, &image.bytes)?;
    finish_png(png)
}

#[cfg(target_os = "linux")]
fn encode_rgba_png(width: usize, height: usize, rgba: &[u8]) -> Result<Vec<u8>, String> {
    let width = u32::try_from(width).map_err(|_| "clipboard image is too wide".to_owned())?;
    let height = u32::try_from(height).map_err(|_| "clipboard image is too tall".to_owned())?;
    let mut encoded = Vec::new();
    {
        let mut encoder = Encoder::new(&mut encoded, width, height);
        encoder.set_color(ColorType::Rgba);
        encoder.set_depth(BitDepth::Eight);
        let mut writer = encoder
            .write_header()
            .map_err(|error| format!("could not encode the clipboard image as PNG: {error}"))?;
        writer
            .write_image_data(rgba)
            .map_err(|error| format!("could not encode the clipboard image as PNG: {error}"))?;
        writer
            .finish()
            .map_err(|error| format!("could not encode the clipboard image as PNG: {error}"))?;
    }
    Ok(encoded)
}

/// Length of the padded standard base64 representation of `bytes`.
#[cfg(target_os = "linux")]
fn base64_encoded_len(bytes: usize) -> usize {
    base64::encoded_len(bytes, true).unwrap_or(usize::MAX)
}

/// Whether a PNG of `png_bytes` fits the raw image cap and the resulting base64
/// IPC payload cap.
#[cfg(target_os = "linux")]
fn png_size_within_limit(png_bytes: usize) -> bool {
    png_bytes <= MAX_CLIPBOARD_IMAGE_BYTES
        && base64_encoded_len(png_bytes) <= MAX_CLIPBOARD_IMAGE_BASE64_BYTES
}

#[cfg(target_os = "linux")]
fn finish_png(png_bytes: Vec<u8>) -> Result<Option<ClipboardImage>, String> {
    if png_bytes.is_empty() {
        return Ok(None);
    }
    if !png_size_within_limit(png_bytes.len()) {
        return Err(format!(
            "clipboard image is too large ({} MB; limit {} MB)",
            png_bytes.len().div_ceil(1024 * 1024),
            MAX_CLIPBOARD_IMAGE_BYTES / (1024 * 1024)
        ));
    }
    let data_base64 = base64::engine::general_purpose::STANDARD.encode(&png_bytes);
    debug_assert_eq!(data_base64.len(), base64_encoded_len(png_bytes.len()));
    Ok(Some(ClipboardImage {
        data_base64,
        mime: "image/png".to_owned(),
        bytes: png_bytes.len(),
    }))
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;

    /// 2x2 RGBA image: red, green, blue, transparent.
    fn sample_rgba() -> (usize, usize, Vec<u8>) {
        (
            2,
            2,
            vec![
                255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 0, 0, 0, 0,
            ],
        )
    }

    #[test]
    fn png_base64_size_helper_counts_padding_and_enforces_limits() {
        assert_eq!(base64_encoded_len(0), 0);
        assert_eq!(base64_encoded_len(1), 4);
        assert_eq!(base64_encoded_len(3), 4);
        assert_eq!(base64_encoded_len(4), 8);
        assert_eq!(
            base64_encoded_len(MAX_CLIPBOARD_IMAGE_BYTES),
            MAX_CLIPBOARD_IMAGE_BASE64_BYTES
        );
        assert!(png_size_within_limit(MAX_CLIPBOARD_IMAGE_BYTES));
        assert!(!png_size_within_limit(MAX_CLIPBOARD_IMAGE_BYTES + 1));

        let (width, height, rgba) = sample_rgba();
        let png = encode_rgba_png(width, height, &rgba).expect("png");
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
        assert!(png_size_within_limit(png.len()));
        assert_eq!(
            base64_encoded_len(png.len()),
            base64::engine::general_purpose::STANDARD
                .encode(&png)
                .len()
        );

        let decoder = png::Decoder::new(std::io::Cursor::new(&png));
        let mut reader = decoder.read_info().expect("png header");
        let mut pixels = vec![0; reader.output_buffer_size().expect("png buffer size")];
        let info = reader.next_frame(&mut pixels).expect("png frame");
        assert_eq!((info.width, info.height), (2, 2));
        assert_eq!(info.color_type, png::ColorType::Rgba);
        assert_eq!(info.bit_depth, png::BitDepth::Eight);
        assert_eq!(&pixels[..info.buffer_size()], rgba.as_slice());
    }

    #[test]
    fn clipboard_images_are_returned_as_size_limited_png_base64() {
        let (width, height, rgba) = sample_rgba();
        let image = finish_image(arboard::ImageData {
            width,
            height,
            bytes: rgba.into(),
        })
        .expect("encoded image")
        .expect("some image");
        assert_eq!(image.mime, "image/png");
        let png = base64::engine::general_purpose::STANDARD
            .decode(image.data_base64)
            .expect("base64");
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));
        assert_eq!(image.bytes, png.len());

        let oversized = vec![0u8; MAX_CLIPBOARD_IMAGE_BYTES + 1];
        assert!(finish_png(oversized).is_err());
        assert!(
            finish_png(Vec::new())
                .expect("empty is not an error")
                .is_none()
        );
    }

    #[test]
    fn malformed_and_oversized_pixel_buffers_are_rejected() {
        let mismatch = finish_image(arboard::ImageData {
            width: 2,
            height: 2,
            bytes: vec![0u8; 4].into(),
        });
        assert!(mismatch.is_err());

        let oversized = finish_image(arboard::ImageData {
            width: MAX_CLIPBOARD_IMAGE_PIXELS + 1,
            height: 1,
            bytes: Vec::new().into(),
        });
        assert!(oversized.is_err());
    }
}
