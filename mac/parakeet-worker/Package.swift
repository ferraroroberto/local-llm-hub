// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ParakeetWorker",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(url: "https://github.com/FluidInference/FluidAudio.git", from: "0.18.0")
    ],
    targets: [
        .executableTarget(
            name: "ParakeetWorker",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")]
        ),
        .executableTarget(
            name: "CanaryWorker",
            dependencies: [.product(name: "FluidAudio", package: "FluidAudio")]
        )
    ]
)
