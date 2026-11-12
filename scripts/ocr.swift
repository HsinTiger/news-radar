// 本機文字辨識（macOS Vision，免費、離線、繁中準）。用法：swift scripts/ocr.swift 圖1 圖2 …
// 每張圖輸出一段文字，圖與圖之間空一行。給投稿截圖用（substack_radar/screenshots.py）。
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let image = NSImage(contentsOfFile: path),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        FileHandle.standardError.write("unreadable: \(path)\n".data(using: .utf8)!)
        continue
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hant", "zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    print(lines.joined(separator: "\n"))
    print("")
}
