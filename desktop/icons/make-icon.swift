// Renders Ostraka's app icons from the clay mark (docs/brand/ostrakon-clay.png).
//
//   swift make-icon.swift tile <mark.png> <out.png>      the 1024 px icon: the sherd on a limestone tile
//   swift make-icon.swift ico <out.ico> <png>...         packs square PNGs into a Windows .ico
//
// The .ico is packed here rather than with Python so the icons build from the Xcode command-line
// tools alone.
import AppKit

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

let args = CommandLine.arguments
guard args.count >= 4 else { fail("usage: make-icon.swift tile <mark.png> <out.png> | ico <out.ico> <png>...") }

switch args[1] {
case "tile":
    guard let mark = NSImage(contentsOfFile: args[2]),
          let markCG = mark.cgImage(forProposedRect: nil, context: nil, hints: nil) else { fail("can't read \(args[2])") }
    let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!
    let size = 1024
    let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0, space: sRGB,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!

    // Apple's icon grid: an 824 px tile 100 px in from each edge, ~185 px corners, so the icon
    // sits at the same size as every other app in the Dock
    let tile = CGRect(x: 100, y: 100, width: 824, height: 824)
    let shape = CGPath(roundedRect: tile, cornerWidth: 185, cornerHeight: 185, transform: nil)

    // A soft drop shadow under the tile, as system icons have
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 24, color: CGColor(gray: 0, alpha: 0.28))
    ctx.addPath(shape)
    ctx.setFillColor(CGColor(srgbRed: 0.945, green: 0.929, blue: 0.894, alpha: 1))
    ctx.fillPath()
    ctx.restoreGState()

    // Limestone (#faf7f1 to #e9e2d5): the clay mark is meant to stand out from a pale ground
    ctx.saveGState()
    ctx.addPath(shape)
    ctx.clip()
    let ground = CGGradient(colorsSpace: sRGB,
                            colors: [CGColor(srgbRed: 0.980, green: 0.969, blue: 0.945, alpha: 1),
                                     CGColor(srgbRed: 0.914, green: 0.886, blue: 0.835, alpha: 1)] as CFArray,
                            locations: [0, 1])!
    ctx.drawLinearGradient(ground, start: CGPoint(x: 0, y: tile.maxY), end: CGPoint(x: 0, y: tile.minY), options: [])

    // The sherd fills most of the tile's width: below about 48 px it turns to texture anyway
    // (docs/BRAND.md), so it's sized for the Dock rather than for 16 px
    let width = tile.width * 0.90
    let height = width * CGFloat(markCG.height) / CGFloat(markCG.width)
    ctx.interpolationQuality = .high
    ctx.draw(markCG, in: CGRect(x: tile.midX - width / 2, y: tile.midY - height / 2, width: width, height: height))
    ctx.restoreGState()

    let rep = NSBitmapImageRep(cgImage: ctx.makeImage()!)
    guard let png = rep.representation(using: .png, properties: [:]) else { fail("can't encode PNG") }
    do { try png.write(to: URL(fileURLWithPath: args[3])) } catch { fail("\(error)") }

case "ico":
    // PNG-compressed entries, which Windows reads at every size since Vista
    let images = args.dropFirst(3).map { path -> (Int, Data) in
        guard let data = FileManager.default.contents(atPath: path),
              let rep = NSBitmapImageRep(data: data) else { fail("can't read \(path)") }
        return (rep.pixelsWide, data)
    }
    var out = Data()
    func le16(_ v: Int) { out.append(contentsOf: [UInt8(v & 0xff), UInt8((v >> 8) & 0xff)]) }
    func le32(_ v: Int) { le16(v & 0xffff); le16((v >> 16) & 0xffff) }
    le16(0); le16(1); le16(images.count)
    var offset = 6 + 16 * images.count
    for (size, data) in images {
        let dim = size >= 256 ? 0 : size  // 0 means 256 in the directory entry
        out.append(contentsOf: [UInt8(dim), UInt8(dim), 0, 0])
        le16(1); le16(32); le32(data.count); le32(offset)
        offset += data.count
    }
    images.forEach { out.append($0.1) }
    do { try out.write(to: URL(fileURLWithPath: args[2])) } catch { fail("\(error)") }

default:
    fail("unknown mode \(args[1])")
}
