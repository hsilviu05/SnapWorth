import SwiftUI

struct OnboardingView: View {
    @State private var vm = OnboardingViewModel()
    var onFinish: () -> Void

    var body: some View {
        ZStack {
            Color.snapBackground.ignoresSafeArea()

            VStack(spacing: 0) {
                // ── Slides ────────────────────────────────────────────────
                TabView(selection: $vm.currentPage) {
                    ForEach(Array(vm.slides.enumerated()), id: \.element.id) { index, slide in
                        SlideView(slide: slide)
                            .tag(index)
                    }
                }
                .tabViewStyle(.page(indexDisplayMode: .never))
                .animation(.spring(duration: 0.4), value: vm.currentPage)

                // ── Page dots ────────────────────────────────────────────
                HStack(spacing: 8) {
                    ForEach(0..<vm.slides.count, id: \.self) { i in
                        Capsule()
                            .fill(i == vm.currentPage ? Color.snapTerracotta : Color.snapBorder)
                            .frame(width: i == vm.currentPage ? 24 : 8, height: 8)
                            .animation(.spring(duration: 0.3), value: vm.currentPage)
                    }
                }
                .padding(.top, 24)

                // ── CTA ───────────────────────────────────────────────────
                VStack(spacing: 12) {
                    PrimaryButton(
                        title: vm.isLastPage ? "Get Started" : "Next"
                    ) {
                        if vm.isLastPage {
                            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                            onFinish()
                        } else {
                            vm.advance()
                        }
                    }

                    if !vm.isLastPage {
                        Button("Skip") { onFinish() }
                            .font(.snapBody)
                            .foregroundStyle(Color.snapWarmGray)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 32)
                .padding(.bottom, 48)
            }
        }
    }
}

// MARK: - Slide View
private struct SlideView: View {
    let slide: OnboardingSlide

    var body: some View {
        VStack(spacing: 32) {
            Spacer()

            // Polaroid-style tilted card
            ZStack {
                // Shadow card (stacked)
                PolaroidCard(symbolName: slide.symbolName, tint: Color.snapBorder)
                    .rotationEffect(.degrees(slide.rotation * 1.6))
                    .offset(x: 12, y: 6)
                    .opacity(0.5)

                // Main card
                PolaroidCard(symbolName: slide.symbolName, tint: Color.snapTerracotta.opacity(0.12))
                    .rotationEffect(.degrees(slide.rotation))
            }
            .padding(.horizontal, 60)

            // Copy
            VStack(spacing: 12) {
                Text(slide.headline)
                    .font(.fraunces(30, weight: .bold))
                    .foregroundStyle(Color.snapEspresso)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)

                Text(slide.body)
                    .font(.snapBody)
                    .foregroundStyle(Color.snapWarmGray)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 28)

            Spacer()
        }
    }
}

private struct PolaroidCard: View {
    let symbolName: String
    let tint: Color

    var body: some View {
        VStack(spacing: 0) {
            // "Photo" area
            ZStack {
                RoundedRectangle(cornerRadius: 4)
                    .fill(tint)
                    .aspectRatio(1, contentMode: .fit)

                Image(systemName: symbolName)
                    .font(.system(size: 52, weight: .light))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(Color.snapTerracotta)
            }

            // "Caption" strip
            Rectangle()
                .fill(Color.white)
                .frame(height: 44)
        }
        .background(Color.white)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 12, x: 0, y: 6)
    }
}
