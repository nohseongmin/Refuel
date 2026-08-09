# iOS guide

> **An iOS app cannot be built on Windows.** Apple requires Xcode, which is macOS only.
> So there are two routes.

## Available today: the web app

iPhone users can use Refuel without any App Store install:

1. Open <https://nohseongmin.github.io/Refuel/> in Safari
2. Share → **Add to Home Screen**
3. It runs full screen, with the dashboard, QR scanning and demo mode all working

Limitation: background push on iOS is restricted. Web push needs iOS 16.4 or newer and
only works once the site is added to the home screen. For reliable alerts you need the
native app below.

## Native iOS app, what it takes

| Item | Detail |
|---|---|
| Apple Developer | **$99 a year**, required |
| Build environment | A Mac with Xcode, or the `ios-build` GitHub Actions workflow in this repo |
| Signing | A certificate (.p12) and a provisioning profile |
| Review risk | **Guideline 4.2.** A thin web view wrapper gets rejected. Refuel has native pieces such as local notifications and camera access to argue with, but iOS review is stricter than Android |

## Verifying the build without a Mac

`.github/workflows/ios.yml` compiles the iOS target on a GitHub macOS runner **without
signing**, which proves the code is iOS-compatible.

- Run it from the Actions tab, workflow `ios-build`, or push a tag: `git tag ios-vX && git push origin ios-vX`
- A green run means the code compiles for iOS.

## Shipping an IPA or TestFlight build

Once you have an Apple account:

1. Join the Apple Developer Program, then register the app in App Store Connect with bundle ID `io.github.nohseongmin.refuel`
2. Create the certificate and profile. Xcode does this for you on a Mac; otherwise use fastlane match
3. Add the certificate as base64, the profile and the passwords to GitHub Secrets
4. Replace the build step in `ios.yml` with a signed build plus `xcodebuild -exportArchive`
5. Upload to TestFlight, run internal testing, submit for review

## Ads on iOS

Ads are **disabled** in this release. If you enable them later:

- `GADApplicationIdentifier` in Info.plist is currently the **iOS test app id**. Register a real iOS app in AdMob and replace it.
- iOS requires **App Tracking Transparency** consent for ad tracking. `NSUserTrackingUsageDescription` is already present.
- Set `ADS_ENABLED` to true in `docs/index.html` and fill in the real ad unit ids.

## Suggested order

1. **Start with the web app on iPhone.** Free and immediate, and it covers most of what people want.
2. Ship on Android and see the response.
3. If there is demand, pay the $99 and go through the signed CI build to the App Store.
