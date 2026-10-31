import SwiftUI

/// The daily guess-the-price game (#411). See `DailyGuess`.
///
/// Opened from the pill on the Scan tab. Nothing here touches the network or
/// the scan allowance, so it is the same for free and Pro users.
struct DailyGuessView: View {
    /// Today, fixed when the sheet opens: a sheet left open past midnight
    /// finishes the day it started.
    private let day: Int
    private let round: DailyGuess.Round?

    @State private var record: DailyGuessRecord
    @State private var guessText = ""
    @State private var shareImage: UIImage?
    @FocusState private var focused: Bool
    @Environment(\.dismiss) private var dismiss

    init(now: Date = Date()) {
        let day = DailyGuess.dayNumber(for: now)
        self.day = day
        self.round = DailyGuess.round(forDay: day)
        _record = State(initialValue: DailyGuessRecord.load())
    }

    private var played: Bool { record.hasPlayed(day: day) }
    private var guess: Double? { MoneyInput.parse(guessText) }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    header
                    if let round {
                        itemCard(round)
                        if played {
                            reveal(round)
                        } else {
                            guessField
                            PrimaryButton(title: "Guess") { submit(round) }
                                .disabled(guess == nil)
                            Text("Never uses a scan.")
                                .font(.snapCaption)
                                .foregroundStyle(Color.snapWarmGray)
                                .frame(maxWidth: .infinity)
                        }
                    }
                }
                .padding(20)
            }
            .background(Color.snapBackground)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .onAppear { if !played { focused = true } }
        .sheet(item: Binding(get: { shareImage.map(ShareImage.init) },
                             set: { shareImage = $0?.image })) { item in
            ActivityShareSheet(items: [item.image]) { _ in
                Analytics.shared.track(.guessCardShared(style: "daily"))
            }
        }
    }

    // ── Pieces ───────────────────────────────────────────────────────

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Daily guess")
                    .font(.snapHeadline)
                    .foregroundStyle(Color.snapEspresso)
                    .accessibilityAddTraits(.isHeader)
                Text("Day \(record.playerDay(on: day))")
                    .font(.snapCaption)
                    .foregroundStyle(Color.snapWarmGray)
            }
            Spacer()
            let streak = record.streak(on: day)
            if streak > 0 {
                Label {
                    Text(verbatim: "\(streak)")
                } icon: {
                    Image(systemName: "flame.fill")
                }
                .font(.snapBodyMedium)
                .foregroundStyle(Color.snapTerracottaText)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(String(localized: "Streak: \(streak)"))
            }
        }
    }

    private func itemCard(_ round: DailyGuess.Round) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let photo = round.photo {
                Image(photo.asset)
                    .resizable()
                    .scaledToFill()
                    .frame(maxWidth: .infinity)
                    .frame(height: 220)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .accessibilityHidden(true)
                photoCredit(photo)
                    .padding(.bottom, 4)
            }
            Text(verbatim: "\(round.category.emoji)  \(round.category.label.uppercased())")
                .font(.snapLabel)
                .foregroundStyle(Color.snapTerracottaText)
            // A brand and product name: the same in every language.
            Text(verbatim: round.item)
                .font(.snapTitle)
                .foregroundStyle(Color.snapEspresso)
                .fixedSize(horizontal: false, vertical: true)
            Text("Condition: \(round.condition)")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
            Text("What does it resell for?")
                .font(.snapBodyMedium)
                .foregroundStyle(Color.snapEspresso)
                .padding(.top, 6)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
    }

    /// The attribution the photo's license asks for, linked to its page.
    @ViewBuilder
    private func photoCredit(_ photo: DailyGuess.Photo) -> some View {
        let label = Text("Photo: \(photo.credit)")
            .font(.snapCaption)
            .foregroundStyle(Color.snapWarmGray)
        if let url = URL(string: photo.source) {
            Link(destination: url) { label.underline() }
        } else {
            label
        }
    }

    private var guessField: some View {
        HStack(spacing: 6) {
            Text(verbatim: "$")
                .font(.snapTitle)
                .foregroundStyle(Color.snapWarmGray)
            TextField("Your guess", text: $guessText)
                .keyboardType(.decimalPad)
                .font(.snapTitle)
                .foregroundStyle(Color.snapEspresso)
                .focused($focused)
                .accessibilityLabel("Your guess in dollars")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
            .strokeBorder(Color.snapControlBorder, lineWidth: 1))
    }

    @ViewBuilder
    private func reveal(_ round: DailyGuess.Round) -> some View {
        let guess = record.lastGuess
        let verdict = DailyGuess.verdict(guess: guess, low: round.low, high: round.high)
        VStack(alignment: .leading, spacing: 10) {
            Group {
                switch verdict {
                case .inRange: Text("In the range!")
                case .close:   Text("Close!")
                case .off:     Text("Not this time")
                }
            }
            .font(.snapTitle)
            .foregroundStyle(verdict == .off ? Color.snapTerracottaText : Color.snapSageText)

            Text("You guessed \(Self.dollars(guess)).")
                .font(.snapBody)
                .foregroundStyle(Color.snapEspresso)
            Text("Typical resale: \(Self.range(round))")
                .font(.snapBodyMedium)
                .foregroundStyle(Color.snapEspresso)
            Text("What moves the price: \(round.why)")
                .font(.snapBody)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
            // The same framing as the website's /guess and /worth pages.
            Text("A typical secondhand range, for guidance. Not sold-listing data.")
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.snapCard)
        .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))

        HStack(spacing: 24) {
            stat(value: record.streak(on: day), label: String(localized: "Streak"))
            stat(value: record.best, label: String(localized: "Best"))
        }
        .frame(maxWidth: .infinity)

        PrimaryButton(title: "Share") { share(round, verdict: verdict) }

        Text("A new item tomorrow.")
            .font(.snapCaption)
            .foregroundStyle(Color.snapWarmGray)
            .frame(maxWidth: .infinity)
    }

    private func stat(value: Int, label: String) -> some View {
        VStack(spacing: 2) {
            Text(verbatim: "\(value)")
                .font(.snapHeadline)
                .foregroundStyle(Color.snapEspresso)
            Text(label)
                .font(.snapCaption)
                .foregroundStyle(Color.snapWarmGray)
        }
        .accessibilityElement(children: .combine)
    }

    // ── Actions ──────────────────────────────────────────────────────

    private func submit(_ round: DailyGuess.Round) {
        guard let guess, guess.isFinite, guess >= 0, !played else { return }
        focused = false
        let next = record.playing(day: day, guess: guess)
        next.save()
        Haptics.selection()
        let verdict = DailyGuess.verdict(guess: guess, low: round.low, high: round.high)
        Analytics.shared.track(.dailyGuessPlayed(
            verdict: verdict.rawValue, streak: DailyGuess.streakBucket(next.streak)))
        withAnimation { record = next }
    }

    private func share(_ round: DailyGuess.Round, verdict: DailyGuess.Verdict) {
        let card = DailyGuessShareCardView(
            day: record.playerDay(on: day), item: round.item, condition: round.condition,
            guess: Self.dollars(record.lastGuess), range: Self.range(round), verdict: verdict,
            percentOff: DailyGuess.percentOff(guess: record.lastGuess, low: round.low, high: round.high),
            streak: record.streak(on: day))
        let renderer = ImageRenderer(content: card)
        renderer.scale = ResultViewModel.shareCardScale
        shareImage = renderer.uiImage
    }

    // ── Formatting ───────────────────────────────────────────────────

    /// Whole dollars: the ranges are whole dollars, and so is the game.
    static func dollars(_ value: Double) -> String {
        guard value.isFinite else { return "$—" }
        return "$\(Int(min(value, 9_999_999).rounded()))"
    }

    static func range(_ round: DailyGuess.Round) -> String {
        "$\(round.low)–$\(round.high)"
    }
}

/// `sheet(item:)` needs something identifiable.
private struct ShareImage: Identifiable {
    let image: UIImage
    var id: ObjectIdentifier { ObjectIdentifier(image) }
}

// MARK: - Share card

/// The 1080×1920 story card for a day's guess, on the same branded canvas as
/// the haul card, with the guess card's QR campaign (`ShareCardKind.guess`).
struct DailyGuessShareCardView: View {
    let day: Int
    let item: String
    let condition: String
    let guess: String
    let range: String
    let verdict: DailyGuess.Verdict
    let percentOff: Int?
    let streak: Int

    static let cardWidth: CGFloat = 540
    static let cardHeight: CGFloat = 960
    private let innerPad: CGFloat = 36

    var body: some View {
        VStack(spacing: 0) {
            Spacer().frame(height: 104)

            Text("DAILY GUESS · DAY \(day)")
                .font(Font.dmSans(16, weight: .bold))
                .tracking(2)
                .foregroundStyle(Color(hex: "8B7D71"))
                .multilineTextAlignment(.center)
                .padding(.horizontal, innerPad)

            Text(verbatim: item)
                .font(Font.fraunces(40, weight: .bold))
                .foregroundStyle(Color(hex: "2B211C"))
                .multilineTextAlignment(.center)
                .minimumScaleFactor(0.5)
                .lineLimit(3)
                .padding(.top, 28)
                .padding(.horizontal, innerPad)

            Text(verbatim: condition)
                .font(Font.dmSans(20))
                .foregroundStyle(Color(hex: "8B7D71"))
                .padding(.top, 8)
                .padding(.horizontal, innerPad)

            Spacer().frame(height: 56)

            Text(verbatim: guess)
                .font(Font.fraunces(84, weight: .bold))
                .foregroundStyle(Color(hex: verdict == .off ? "C4562F" : "6F8F6B"))
                .minimumScaleFactor(0.4)
                .lineLimit(1)
                .padding(.horizontal, innerPad)

            Group {
                switch verdict {
                case .inRange: Text("My guess: in the range")
                case .close:
                    if let percentOff { Text("My guess: within \(Self.percent(percentOff))") }
                    else { Text("My guess: close") }
                case .off:     Text("My guess: not this time")
                }
            }
            .font(Font.dmSans(20, weight: .semibold))
            .foregroundStyle(Color(hex: "2B211C"))
            .padding(.top, 6)

            Text("Typical resale: \(range)")
                .font(Font.dmSans(18))
                .foregroundStyle(Color(hex: "8B7D71"))
                .padding(.top, 14)

            if streak > 1 {
                Text("🔥 Streak: \(streak)")
                    .font(Font.dmSans(18, weight: .semibold))
                    .foregroundStyle(Color(hex: "2B211C"))
                    .padding(.top, 24)
            }

            Spacer()

            Text("Can you beat it?")
                .font(Font.fraunces(26, weight: .bold))
                .foregroundStyle(Color(hex: "2B211C"))
                .padding(.bottom, 20)

            Rectangle()
                .fill(Color(hex: "EFE6DC"))
                .frame(height: 1)
                .padding(.horizontal, innerPad)

            HStack(spacing: 14) {
                if let qr = snapShareCardQR(for: .guess) {
                    Image(uiImage: qr)
                        .interpolation(.none)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 48, height: 48)
                        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(verbatim: "SnapWorth")
                        .font(Font.fraunces(20, weight: .bold))
                        .foregroundStyle(Color(hex: "2B211C"))
                    Text("Get SnapWorth")
                        .font(Font.dmSans(14))
                        .foregroundStyle(Color(hex: "8B7D71"))
                }
                Spacer()
            }
            .padding(.horizontal, innerPad)
            .padding(.top, 16)
            .padding(.bottom, 44)
        }
        .frame(width: Self.cardWidth, height: Self.cardHeight)
        .background(Color(hex: "FBF7F2"))
    }

    /// "8%": a number and a sign, the same in every language the app speaks.
    static func percent(_ value: Int) -> String { "\(value)%" }
}

// MARK: - Entry

/// The Scan tab's way in, beside Haul and Rack. Marked until today is played.
struct DailyGuessPill: View {
    let isDisabled: Bool
    /// The game's sheet is up. Read again when it closes, since the pill stays
    /// on screen underneath and `onAppear` does not run.
    let isPresenting: Bool
    let action: () -> Void

    @State private var playedToday = false

    var body: some View {
        Button {
            Haptics.selection()
            action()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "dollarsign.circle.fill")
                    .snapSymbol(13, weight: .semibold, relativeTo: .footnote)
                    .accessibilityHidden(true)
                Text("Daily guess")
                if !playedToday {
                    Circle()
                        .fill(Color.snapAmber)
                        .frame(width: 7, height: 7)
                        .accessibilityHidden(true)
                }
            }
            .lineLimit(1)
            .font(.snapCaption.bold())
            .foregroundStyle(Color.snapOnCharcoal.opacity(isDisabled ? 0.4 : 0.95))
            .padding(.horizontal, 14)
            .padding(.vertical, 6)
            .background(Color.snapCharcoal.opacity(0.5))
            .clipShape(Capsule())
        }
        .disabled(isDisabled)
        .snapHitTarget()
        .accessibilityElement(children: .combine)
        .accessibilityValue(playedToday ? String(localized: "Played today") : String(localized: "Not played today"))
        .accessibilityHint("Guess what today's item resells for. Never uses a scan.")
        .onAppear(perform: refresh)
        .onChange(of: isPresenting) { _, shown in if !shown { refresh() } }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            refresh()
        }
    }

    func refresh() {
        playedToday = DailyGuessRecord.load().hasPlayed(day: DailyGuess.dayNumber(for: Date()))
    }
}
