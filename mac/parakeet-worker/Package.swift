// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ParakeetWorker",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/FluidInference/FluidAudio.git", from: "0.17.3")
    ],
    targets: [
        .executableTarget(
            name: "ParakeetWorker",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")]
        )
    ]
)
