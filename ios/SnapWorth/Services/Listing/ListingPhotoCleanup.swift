import CoreGraphics
import CoreImage
import UIKit
import Vision

/// Listing photo cleanup (#91): lift the item off a thrift-store background
/// and set it on a plain backdrop, sized for the marketplace.
///
/// Entirely on-device. No upload, no backend cost, no new privacy question.
/// The cleaned image is an export, never a replacement: `ScanResult.imageData`
/// is not touched by anything here.
///
/// Two stages, split on purpose:
///
/// * `cutOut` runs Vision's foreground mask and crops the item. It is the slow
///   part (the model), and the only part that needs a real device:
///   `VNGenerateForegroundInstanceMaskRequest` fails in the simulator with
///   "Could not create inference context".
/// * `compose` places a cut-out on a canvas and backdrop. It is pure
///   CoreGraphics, fast enough to re-run on every marketplace or backdrop
///   change, and it is what the tests can exercise in CI.
enum ListingPhotoCleanup {

    /// Why a photo was left as it was. The user sees one message for all of
    /// them; the distinction is for tests and logs.
    enum Fallback: Error, Equatable {
        /// Vision found no foreground instance.
        case noSubject
        /// A mask came back, but covering so little or so much of the photo
        /// that it is noise or the whole frame. Shipping it would be a
        /// half-cut item, which the issue rules out.
        case lowConfidence
        /// The request itself failed (including every simulator run).
        case failed
    }

    /// The item, already cut out: transparent everywhere but the subject, and
    /// cropped to the subject's bounds.
    struct CutOut {
        let image: CGImage
    }

    /// Accept a mask only when it covers between these fractions of the photo.
    /// Below, it is a speck; above, it is "everything", i.e. no separation.
    static let minimumCoverage = 0.01
    static let maximumCoverage = 0.97

    // MARK: Stage 1 — cut out

    /// Cut the item out of `photo`. Never throws past this point: every
    /// failure is a `Fallback`, and the caller keeps the original.
    static func cutOut(_ photo: UIImage,
                       masker: any ForegroundMasking = VisionForegroundMasker()) async -> Result<CutOut, Fallback> {
        // Off the main actor: the Vision request and the pixel walk below are
        // both heavy, for the same reason `encodeForStorage` is detached.
        await Task.detached(priority: .userInitiated) {
            guard let image = uprightCGImage(photo) else { return .failure(.failed) }
            let mask: CGImage?
            do {
                mask = try masker.mask(for: image)
            } catch {
                return .failure(.failed)
            }
            guard let mask else { return .failure(.noSubject) }
            return cutOut(image, mask: mask)
        }.value
    }

    /// The synchronous core, given a mask. Internal so tests can supply masks.
    static func cutOut(_ image: CGImage, mask: CGImage) -> Result<CutOut, Fallback> {
        let width = image.width, height = image.height
        guard width > 0, height > 0,
              let gray = grayBytes(mask, width: width, height: height) else {
            return .failure(.failed)
        }

        // Bounds and coverage of the opaque part of the mask, rows top-down.
        var minX = width, minY = height, maxX = -1, maxY = -1, covered = 0
        for y in 0..<height {
            let row = y * width
            for x in 0..<width where gray[row + x] > 127 {
                covered += 1
                if x < minX { minX = x }
                if x > maxX { maxX = x }
                if y < minY { minY = y }
                if y > maxY { maxY = y }
            }
        }
        guard covered > 0 else { return .failure(.noSubject) }
        let coverage = Double(covered) / Double(width * height)
        guard coverage >= minimumCoverage, coverage <= maximumCoverage else {
            return .failure(.lowConfidence)
        }

        // Draw the photo through the mask, then crop to the subject.
        guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                                  bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue),
              let maskImage = grayMaskImage(gray, width: width, height: height)
        else { return .failure(.failed) }
        let full = CGRect(x: 0, y: 0, width: width, height: height)
        ctx.clip(to: full, mask: maskImage)
        ctx.draw(image, in: full)
        let bounds = CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
        guard let masked = ctx.makeImage(), let cropped = masked.cropping(to: bounds) else {
            return .failure(.failed)
        }
        return .success(CutOut(image: cropped))
    }

    // MARK: Stage 2 — compose

    /// Set a cut-out on `backdrop`, fitted inside `canvas` with a margin.
    static func compose(_ cutOut: CutOut, canvas: ListingPhotoCanvas,
                        backdrop: ListingPhotoBackdrop) -> UIImage {
        let size = canvas.pixelSize
        let format = UIGraphicsImageRendererFormat.default()
        // Points == pixels, as in `ScanAPIClient.downscale`: the default screen
        // scale would silently export a 3× bitmap.
        format.scale = 1
        format.opaque = true

        let margin = min(size.width, size.height) * canvas.marginFraction
        let room = CGRect(origin: .zero, size: size).insetBy(dx: margin, dy: margin)
        let subject = CGSize(width: cutOut.image.width, height: cutOut.image.height)
        let scale = min(room.width / subject.width, room.height / subject.height)
        let drawn = CGSize(width: subject.width * scale, height: subject.height * scale)
        let origin = CGPoint(x: (size.width - drawn.width) / 2, y: (size.height - drawn.height) / 2)

        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            backdrop.color.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            UIImage(cgImage: cutOut.image).draw(in: CGRect(origin: origin, size: drawn))
        }
    }

    // MARK: Helpers

    /// The photo as an upright bitmap. `UIImage.cgImage` ignores
    /// `imageOrientation`, so a portrait camera photo would be masked sideways.
    static func uprightCGImage(_ photo: UIImage) -> CGImage? {
        if photo.imageOrientation == .up, let cg = photo.cgImage { return cg }
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let size = CGSize(width: photo.size.width * photo.scale, height: photo.size.height * photo.scale)
        return UIGraphicsImageRenderer(size: size, format: format).image { _ in
            photo.draw(in: CGRect(origin: .zero, size: size))
        }.cgImage
    }

    /// `mask` redrawn as one byte per pixel at `width × height`, rows top-down.
    private static func grayBytes(_ mask: CGImage, width: Int, height: Int) -> [UInt8]? {
        var bytes = [UInt8](repeating: 0, count: width * height)
        let drawn = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let ctx = CGContext(data: buffer.baseAddress, width: width, height: height,
                                      bitsPerComponent: 8, bytesPerRow: width,
                                      space: CGColorSpaceCreateDeviceGray(),
                                      bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            ctx.interpolationQuality = .high
            ctx.draw(mask, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        return drawn ? bytes : nil
    }

    private static func grayMaskImage(_ bytes: [UInt8], width: Int, height: Int) -> CGImage? {
        guard let provider = CGDataProvider(data: Data(bytes) as CFData) else { return nil }
        return CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 8,
                       bytesPerRow: width, space: CGColorSpaceCreateDeviceGray(),
                       bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                       provider: provider, decode: nil, shouldInterpolate: true,
                       intent: .defaultIntent)
    }
}

// MARK: - Canvas and backdrop

/// The export's shape. Poshmark and Mercari show listing photos square; Depop
/// shows them 4:5 portrait (#91). The other marketplaces have no single
/// display shape, and square crops safely into every one of them.
enum ListingPhotoCanvas: Equatable {
    case square
    case portrait4x5

    init(marketplace: Marketplace) {
        self = marketplace == .depop ? .portrait4x5 : .square
    }

    var pixelSize: CGSize {
        switch self {
        case .square:      CGSize(width: 1080, height: 1080)
        case .portrait4x5: CGSize(width: 1080, height: 1350)
        }
    }

    /// Clear space around the item, as a fraction of the canvas's short side.
    var marginFraction: CGFloat { 0.08 }
}

enum ListingPhotoBackdrop: String, CaseIterable, Identifiable {
    case white
    case softGrey

    var id: String { rawValue }

    var color: UIColor {
        switch self {
        case .white:    UIColor(white: 1, alpha: 1)
        case .softGrey: UIColor(red: 0.945, green: 0.941, blue: 0.933, alpha: 1)
        }
    }

    var label: String {
        switch self {
        case .white:    String(localized: "White")
        case .softGrey: String(localized: "Soft grey")
        }
    }
}

// MARK: - Masking

/// Where a foreground mask comes from. Vision in the app; a fixed mask in
/// tests, because Vision cannot run in the simulator.
protocol ForegroundMasking: Sendable {
    /// A mask the size of `image` (white = item), or nil when there is no
    /// foreground. Throws when the request itself fails.
    func mask(for image: CGImage) throws -> CGImage?
}

struct VisionForegroundMasker: ForegroundMasking {
    func mask(for image: CGImage) throws -> CGImage? {
        let request = VNGenerateForegroundInstanceMaskRequest()
        let handler = VNImageRequestHandler(cgImage: image)
        try handler.perform([request])
        guard let observation = request.results?.first, !observation.allInstances.isEmpty else {
            return nil
        }
        // Every instance: a pair of shoes is two instances and one listing.
        let buffer = try observation.generateScaledMaskForImage(
            forInstances: observation.allInstances, from: handler)
        let ci = CIImage(cvPixelBuffer: buffer)
        return CIContext().createCGImage(ci, from: ci.extent, format: .L8,
                                         colorSpace: CGColorSpaceCreateDeviceGray())
    }
}
