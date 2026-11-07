import Darwin
import FluidAudio
import Foundation

// Sibling of ParakeetWorker: same line protocol (one audio path per line in, one
// JSON object per line out, "READY" once the models are loaded), but it runs
// Canary-1b-v2 speech translation instead of Parakeet transcription. A separate
// process keeps the live audio_transcribe worker untouched.
//
//   CanaryWorker [--source es] [--target en]     (source == target transcribes)

struct CanaryResponse: Codable {
    let ok: Bool
    let text: String?
    let elapsedS: Double?
    let durationS: Double?
    let peakRssMb: Double?
    let vad: Bool?
    let error: String?
}

func emit(_ resp: CanaryResponse) {
    if let data = try? JSONEncoder().encode(resp), let line = String(data: data, encoding: .utf8) {
        print(line)
    } else {
        print("{\"ok\":false,\"error\":\"encode_failed\"}")
    }
    fflush(stdout)
}

func log(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

// ru_maxrss is bytes on macOS; this is the process high-water mark, so it
// includes model load and every clip decoded so far.
func peakRssMb() -> Double {
    var usage = rusage()
    getrusage(RUSAGE_SELF, &usage)
    return Double(usage.ru_maxrss) / 1_048_576.0
}

func languageArg(_ flag: String, default fallback: CanaryLanguage) -> CanaryLanguage? {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return fallback }
    return CanaryLanguage(rawValue: args[i + 1])
}

@main
struct CanaryWorker {
    static func main() async {
        guard let source = languageArg("--source", default: .spanish),
            let target = languageArg("--target", default: .english)
        else {
            log("FATAL: unknown --source/--target language code")
            exit(2)
        }
        log("Loading Canary-1b-v2 (\(source.rawValue) -> \(target.rawValue))...")
        do {
            let loadStart = Date()
            let manager = try await CanaryManager.load(source: source, target: target)
            // Silero VAD splits audio over one 15 s window at pauses; translated
            // text is not token-stable, so the overlap/seam-merge path is unsafe.
            let vad = try await VadManager()
            log("READY load_s=\(Date().timeIntervalSince(loadStart)) peak_rss_mb=\(peakRssMb())")
            print("READY")
            fflush(stdout)

            let converter = AudioConverter(sampleRate: Double(CanaryConfig.sampleRate))
            while let line = readLine(strippingNewline: true) {
                let path = line.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !path.isEmpty else { continue }
                let start = Date()
                do {
                    let samples = try converter.resampleAudioFile(URL(fileURLWithPath: path))
                    let useVad = samples.count > CanaryConfig.maxSamples
                    let text =
                        useVad
                        ? try await manager.transcribe(audio: samples, vad: vad)
                        : try await manager.transcribe(audio: samples)
                    emit(
                        CanaryResponse(
                            ok: true, text: text, elapsedS: Date().timeIntervalSince(start),
                            durationS: Double(samples.count) / Double(CanaryConfig.sampleRate),
                            peakRssMb: peakRssMb(), vad: useVad, error: nil))
                } catch {
                    emit(
                        CanaryResponse(
                            ok: false, text: nil, elapsedS: nil, durationS: nil, peakRssMb: nil,
                            vad: nil, error: "\(error)"))
                }
            }
        } catch {
            log("FATAL: \(error)")
            exit(1)
        }
    }
}
