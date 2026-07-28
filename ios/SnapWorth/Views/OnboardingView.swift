import SwiftUI

struct OnboardingView: View {
    @State private var vm = OnboardingViewModel()
    var onFinish: () -> Void

    var body: some View {
        ZStack {
            Color.snapBackground.ignoresSafeArea()

            VStack(spacing: 0) {
                // ── Skip (top-right, hidden on last page) ─────────────────
                HStack {
                    Spacer()
                    if !vm.isLastPage {
                        Button("Skip") { onFinish() }
                            .font(.dmSans(15, weight: .medium))
                            .foregroundStyle(Color.snapWarmGray)
                            .transition(.opacity)
                    }
                }
                .frame(height: 24)
                .padding(.horizontal, 24)
                .padding(.top, 8)
                .snapAnimation(.easeInOut(duration: 0.2), value: vm.isLastPage)

                // ── Slides ────────────────────────────────────────────────
                TabView(selection: $vm.currentPage) {
                    ForEach(Array(vm.slides.enumerated()), id: \.element.id) { index, slide in
                        SlideView(slide: slide, isActive: index == vm.currentPage)
                            .tag(index)
                    }
                }
                .tabViewStyle(.page(indexDisplayMode: .never))
                .snapAnimation(.spring(duration: 0.4), value: vm.currentPage)

                // ── Page dots ────────────────────────────────────────────
                HStack(spacing: 8) {
                    ForEach(0..<vm.slides.count, id: \.self) { i in
                        Capsule()
                            .fill(i == vm.currentPage ? Color.snapTerracotta : Color.snapBorder)
                            .frame(width: i == vm.currentPage ? 24 : 8, height: 8)
                            .snapAnimation(.spring(duration: 0.3), value: vm.currentPage)
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Page \(vm.currentPage + 1) of \(vm.slides.count)")
                .padding(.top, 24)

                // ── CTA ───────────────────────────────────────────────────
                VStack(spacing: 12) {
                    PrimaryButton(
                        title: vm.isLastPage ? "Start scanning" : "Next"
                    ) {
                        if vm.isLastPage {
                            Haptics.capture()
                            onFinish()
                        } else {
                            vm.advance()
                        }
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
    let isActive: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        // Scrollable so accessibility text sizes overflow gracefully instead of
        // clipping the hero and truncating the body copy. When the content fits
        // (the common case) `minHeight` keeps it optically centred as before.
        GeometryReader { proxy in
            ScrollView(.vertical, showsIndicators: false) {
                VStack(spacing: 32) {
                    OnboardingHeroView(slide: slide)
                        .scaleEffect(isActive ? 1 : 0.94)
                        .opacity(isActive ? 1 : 0.55)
                        .animation(
                            reduceMotion ? nil : .spring(response: 0.5, dampingFraction: 0.82),
                            value: isActive
                        )

                    VStack(spacing: 12) {
                        Text(slide.headline)
                            .font(.fraunces(30, weight: .bold, relativeTo: .title))
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
                    // One VoiceOver stop per slide, read as a single sentence.
                    .accessibilityElement(children: .combine)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 24)
                .frame(minHeight: proxy.size.height)
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════
// MARK: - Hero illustrations — previews of the real product
// ═══════════════════════════════════════════════════════════════════

private struct OnboardingHeroView: View {
    let slide: OnboardingSlide
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var float = false

    var body: some View {
        content
            // The hero is an *illustration of the UI*, not readable content —
            // like an image of text, it must not scale with Dynamic Type or it
            // overflows its own mock card. The real copy below scales freely.
            .dynamicTypeSize(...DynamicTypeSize.large)
            .frame(height: 220)
            // Decorative idle motion — skipped entirely under Reduce Motion.
            .offset(y: reduceMotion ? 0 : (float ? -6 : 6))
            .animation(
                reduceMotion ? nil : .easeInOut(duration: 2.6).repeatForever(autoreverses: true),
                value: float
            )
            .onAppear {
                guard !reduceMotion else { return }
                float = true
            }
            // The hero is a decorative preview of the copy below it; VoiceOver
            // should read the headline, not enumerate the mock card's contents.
            .accessibilityHidden(true)
    }

    @ViewBuilder private var content: some View {
        switch slide.hero {
        case .valueEstimate: ValueEstimateHero(accent: slide.accent)
        case .snapSell:      SnapSellHero(accent: slide.accent)
        case .thriftFlip:    ThriftFlipHero(accent: slide.accent)
        case .trackFinds:    TrackFindsHero(accent: slide.accent)
        }
    }
}

// A mock result card — mirrors the real ResultView value moment.
private struct ValueEstimateHero: View {
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // "Photo" area
            ZStack {
                LinearGradient(
                    colors: [accent.opacity(0.22), accent.opacity(0.08)],
                    startPoint: .topLeading, endPoint: .bottomTrailing
                )
                Image(systemName: "tshirt.fill")
                    .snapSymbol(46, weight: .light)
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(accent)
            }
            .frame(height: 108)

            VStack(alignment: .leading, spacing: 8) {
                Text("Vintage Wool Blazer")
                    .font(.dmSans(13, weight: .medium))
                    .foregroundStyle(Color.snapEspresso)

                Text("$45–$90")
                    .font(.fraunces(26, weight: .bold))
                    .foregroundStyle(Color.snapSage)
                    .fixedSize()

                ConfidenceBadge(confidence: "High")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
        }
        .frame(width: 260)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 20, x: 0, y: 10)
        .rotationEffect(.degrees(-2))
    }
}

// A mock listing draft — mirrors the Snap → Sell card.
private struct SnapSellHero: View {
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Listing Draft")
                    .font(.snapLabel)
                    .foregroundStyle(Color.snapWarmGray)
                Spacer()
                Image(systemName: "sparkle")
                    .snapSymbol(13)
                    .foregroundStyle(accent)
            }

            Text("Vintage Wool Blazer — Timeless Tailored Fit")
                .font(.dmSans(14, weight: .semibold))
                .foregroundStyle(Color.snapEspresso)
                .lineLimit(2)
                .multilineTextAlignment(.leading)

            // Redacted body lines
            VStack(alignment: .leading, spacing: 6) {
                ForEach([210.0, 178.0, 128.0], id: \.self) { w in
                    Capsule()
                        .fill(Color.snapBorder)
                        .frame(width: w, height: 7)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            // Marketplace chips
            HStack(spacing: 6) {
                ChipView(label: "eBay", color: accent.opacity(0.14), textColor: accent)
                ChipView(label: "Vinted", color: Color.snapBorder)
                ChipView(label: "Facebook", color: Color.snapBorder)
            }
            .padding(.top, 2)
        }
        .padding(16)
        .frame(width: 260)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 20, x: 0, y: 10)
        .rotationEffect(.degrees(2))
    }
}

// A mock profit verdict — mirrors the Thrift Flip result.
private struct ThriftFlipHero: View {
    let accent: Color

    var body: some View {
        VStack(spacing: 14) {
            HStack {
                priceBlock(label: "You pay", value: "$8")
                Image(systemName: "arrow.right")
                    .snapSymbol(13, weight: .semibold)
                    .foregroundStyle(Color.snapWarmGray)
                priceBlock(label: "Resell", value: "$45")
            }

            Divider().overlay(Color.snapBorder)

            VStack(spacing: 4) {
                Text("+$32 profit")
                    .font(.fraunces(30, weight: .bold))
                    .foregroundStyle(Color.snapSage)
                Text("after marketplace fees")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }

            Label("Worth flipping", systemImage: "checkmark.circle.fill")
                .font(.dmSans(13, weight: .semibold))
                .foregroundStyle(Color.snapSage)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(Color.snapSage.opacity(0.14))
                .clipShape(Capsule())
        }
        .padding(18)
        .frame(width: 260)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.12), radius: 20, x: 0, y: 10)
    }

    private func priceBlock(label: String, value: String) -> some View {
        VStack(spacing: 2) {
            Text(label)
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
            Text(value)
                .font(.dmSans(19, weight: .bold))
                .foregroundStyle(Color.snapEspresso)
        }
        .frame(maxWidth: .infinity)
    }
}

// A mock closet grid — mirrors My Finds.
private struct TrackFindsHero: View {
    let accent: Color

    private let items: [(String, String, String)] = [
        ("tshirt.fill", "Wool Blazer", "$45–$90"),
        ("bag.fill", "Leather Tote", "$60–$120"),
        ("shoe.fill", "Retro Sneakers", "$30–$70"),
        ("eyeglasses", "Vintage Frames", "$25–$55"),
    ]

    var body: some View {
        LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: 6) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(accent.opacity(0.12))
                        Image(systemName: item.0)
                            .snapSymbol(20, weight: .light)
                            .foregroundStyle(accent)
                    }
                    .frame(height: 48)

                    Text(item.1)
                        .font(.dmSans(11, weight: .medium))
                        .foregroundStyle(Color.snapEspresso)
                        .lineLimit(1)
                    Text(item.2)
                        .font(.fraunces(12, weight: .bold))
                        .foregroundStyle(Color.snapSage)
                }
                .padding(8)
                .background(Color.snapCard)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
        }
        .frame(width: 260)
        .padding(12)
        .background(Color.snapBackground)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        .shadow(color: Color.snapCardShadow.opacity(0.10), radius: 18, x: 0, y: 8)
    }
}
