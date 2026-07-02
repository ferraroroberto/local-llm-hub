import Foundation
import FluidAudio

struct AsrResponse: Codable {
    let ok: Bool
    let text: String?
    let elapsedS: Double?
    let durationS: Double?
    let confidence: Float?
    let error: String?
}

func emit(_ resp: AsrResponse) {
    let encoder = JSONEncoder()
    if let data = try? encoder.encode(resp), let line = String(data: data, encoding: .utf8) {
        print(line)
    } else {
        print("{\"ok\":false,\"error\":\"encode_failed\"}")
    }
    fflush(stdout)
}

@main
struct ParakeetWorker {
    static func main() async {
        FileHandle.standardError.write("Loading Parakeet TDT v3 models...\n".data(using: .utf8)!)
        do {
            let models = try await AsrModels.downloadAndLoad(version: .v3)
            let asrManager = AsrManager(config: .default)
            try await asrManager.loadModels(models)
            FileHandle.standardError.write("READY\n".data(using: .utf8)!)
            print("READY")
            fflush(stdout)

            while let line = readLine(strippingNewline: true) {
                let path = line.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !path.isEmpty else { continue }
                let url = URL(fileURLWithPath: path)
                var decoderState = TdtDecoderState.make(decoderLayers: await asrManager.decoderLayerCount)
                let start = Date()
                do {
                    let result = try await asrManager.transcribe(url, decoderState: &decoderState)
                    let elapsed = Date().timeIntervalSince(start)
                    emit(AsrResponse(ok: true, text: result.text, elapsedS: elapsed, durationS: result.duration, confidence: result.confidence, error: nil))
                } catch {
                    emit(AsrResponse(ok: false, text: nil, elapsedS: nil, durationS: nil, confidence: nil, error: "\(error)"))
                }
            }
        } catch {
            FileHandle.standardError.write("FATAL: \(error)\n".data(using: .utf8)!)
            exit(1)
        }
    }
}
