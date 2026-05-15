#!/usr/bin/swift

import Vision
import AppKit
import Foundation

/// OCR 辅助：从截图中识别指定文字并返回坐标
/// 用法: swift ocr_helper.swift <png_path> <target_text> [--prefer-group-result|--prefer-contact-result|--verify-chat-title] [--require-exact]

guard CommandLine.arguments.count >= 3 else {
    print("usage: ocr_helper.swift <png_path> <target_text>")
    exit(1)
}

let pngPath = CommandLine.arguments[1]
let targetText = CommandLine.arguments[2]
let preferGroupResult = CommandLine.arguments.contains("--prefer-group-result")
let preferContactResult = CommandLine.arguments.contains("--prefer-contact-result")
let verifyChatTitle = CommandLine.arguments.contains("--verify-chat-title")
let requireExact = CommandLine.arguments.contains("--require-exact")

guard let nsImage = NSImage(contentsOfFile: pngPath) else {
    print("load_image_failed")
    exit(1)
}

guard let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print("cgImage_failed")
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "zh-Hant"]

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
do {
    try handler.perform([request])
} catch {
    print("ocr_error:\(error)")
    exit(1)
}

guard let observations = request.results else {
    print("no_results")
    exit(0)
}

let imgW = CGFloat(cgImage.width)
let imgH = CGFloat(cgImage.height)

struct Match {
    let x: CGFloat
    let y: CGFloat
    let text: String
    let exact: Bool
}

func printMatch(_ match: Match) -> Never {
    print(String(format: "%.4f,%.4f,%@", match.x, match.y, match.text))
    exit(0)
}

var matches: [Match] = []
var groupLabelY: CGFloat?
var chatRecordLabelY: CGFloat?

func normalized(_ text: String) -> String {
    return text
        .replacingOccurrences(of: " ", with: "")
        .replacingOccurrences(of: "　", with: "")
}

func isUsefulPartialMatch(_ text: String, target: String) -> Bool {
    let cleanText = normalized(text)
    let cleanTarget = normalized(target)
    if cleanText.count < 4 { return false }
    if cleanText.contains(cleanTarget) || cleanTarget.contains(cleanText) { return true }
    let prefix = String(cleanTarget.prefix(min(6, cleanTarget.count)))
    return cleanText.contains(prefix)
}

for obs in observations {
    guard let candidate = obs.topCandidates(1).first else { continue }
    let text = candidate.string
    let bbox = obs.boundingBox
    let midX = bbox.midX
    let midY = 1.0 - bbox.midY

    if text.contains("群聊") {
        groupLabelY = midY
    }
    if text.contains("聊天记录") {
        chatRecordLabelY = midY
    }

    if text.contains(targetText) || isUsefulPartialMatch(text, target: targetText) {
        matches.append(Match(x: midX, y: midY, text: text, exact: text.contains(targetText)))
    }
}

if requireExact {
    matches = matches.filter { $0.exact }
}

if verifyChatTitle {
    let searchPageMarkers = ["AI搜索", "搜索网络结果", "文章", "账号", "朋友圈", "听一听", "新闻"]
    let allText = observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: " ")
    if searchPageMarkers.contains(where: { allText.contains($0) }) {
        print("not_found")
        exit(0)
    }

    let titleMatches = matches.filter {
        // 当前聊天标题在主窗口顶部左侧，避开左侧会话列表和正文消息区。
        $0.x > 0.24 && $0.y < 0.16
    }
    if let match = titleMatches.sorted(by: {
        if $0.exact != $1.exact { return $0.exact && !$1.exact }
        return $0.y < $1.y
    }).first {
        printMatch(match)
    }
    print("not_found")
    exit(0)
}

if preferGroupResult, !matches.isEmpty {
    // 微信搜索面板里同一群名可能出现三次:
    // 搜索框/网络结果在顶部，群聊结果在中上部，聊天记录在更下方。
    // 优先选择「群聊」标题下方且「聊天记录」标题上方的候选。
    let groupTop = groupLabelY ?? 0.12
    let groupBottom = chatRecordLabelY ?? 0.52
    let groupBandMatches = matches.filter {
        !$0.text.hasPrefix("Q ") && $0.y > groupTop && $0.y < groupBottom
    }
    if let match = groupBandMatches.sorted(by: { $0.y < $1.y }).first {
        printMatch(match)
    }
    if let match = matches.filter({ !$0.text.hasPrefix("Q ") && $0.y > groupTop }).sorted(by: { $0.y < $1.y }).first {
        printMatch(match)
    }
}

if preferContactResult, !matches.isEmpty {
    // 私聊搜索同样可能匹配到顶部搜索框或聊天记录。
    // 联系人结果通常位于搜索框下方、聊天记录标题上方。
    let contactTop: CGFloat = 0.10
    let contactBottom = chatRecordLabelY ?? 0.58
    let contactMatches = matches.filter {
        !$0.text.hasPrefix("Q ") && $0.y > contactTop && $0.y < contactBottom
    }
    if let match = contactMatches.sorted(by: {
        if $0.exact != $1.exact { return $0.exact && !$1.exact }
        return $0.y < $1.y
    }).first {
        printMatch(match)
    }
    if let match = matches.filter({ !$0.text.hasPrefix("Q ") && $0.y > contactTop }).sorted(by: {
        if $0.exact != $1.exact { return $0.exact && !$1.exact }
        return $0.y < $1.y
    }).first {
        printMatch(match)
    }
}

if let match = matches.first {
    printMatch(match)
}

print("not_found")
