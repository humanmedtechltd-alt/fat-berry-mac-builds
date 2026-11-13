// Opens an installed Audio Unit's editor the way Logic does and reports what is on screen.
//
//   clang++ -std=c++20 -fobjc-arc -framework Cocoa -framework AudioToolbox -framework CoreAudioKit \
//     au_view_probe.mm -o au_view_probe
//   au_view_probe aumu F985 FtBy window.png
//
// Logic asks the Audio Unit for its Cocoa view factory and calls uiViewForAudioUnit, which for iPlug2 calls
// OpenWindow(nullptr): no parent view. The probe does exactly that, puts the returned view in a window, waits,
// then prints the view tree (class, frame, hidden) and how much of a capture of the window is not black.
#import <AudioToolbox/AudioToolbox.h>
#import <AudioUnit/AUCocoaUIView.h>
#import <Cocoa/Cocoa.h>
#include <cstdio>
#include <cstdlib>
#include <string>

static OSType fourcc(const char* s) {
  return (OSType(s[0]) << 24) | (OSType(s[1]) << 16) | (OSType(s[2]) << 8) | OSType(s[3]);
}

static void dump(NSView* view, int depth, std::string& out) {
  if (depth > 6) return;
  NSRect f = view.frame;
  char line[512];
  std::snprintf(line, sizeof line, "%*s%s frame=%.0fx%.0f at %.0f,%.0f hidden=%s\n", depth * 2, "",
                NSStringFromClass([view class]).UTF8String, f.size.width, f.size.height, f.origin.x, f.origin.y,
                view.hidden ? "yes" : "no");
  out += line;
  for (NSView* child in view.subviews) dump(child, depth + 1, out);
}

static void spin(double seconds) {
  [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:seconds]];
}

int main(int argc, char** argv) {
  @autoreleasepool {
    if (argc != 5) { std::fprintf(stderr, "usage: au_view_probe TYPE SUBTYPE MANUFACTURER out.png\n"); return 2; }
    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    AudioComponentDescription desc = {fourcc(argv[1]), fourcc(argv[2]), fourcc(argv[3]), 0, 0};
    AudioComponent component = AudioComponentFindNext(nullptr, &desc);
    if (!component) { std::printf("{\"error\":\"component not found\"}\n"); return 1; }
    AudioUnit unit = nullptr;
    if (AudioComponentInstanceNew(component, &unit) != noErr || !unit) { std::printf("{\"error\":\"instance failed\"}\n"); return 1; }
    AudioUnitInitialize(unit);
    UInt32 size = 0;
    Boolean writable = false;
    if (AudioUnitGetPropertyInfo(unit, kAudioUnitProperty_CocoaUI, kAudioUnitScope_Global, 0, &size, &writable) != noErr || !size) {
      std::printf("{\"error\":\"no Cocoa UI\"}\n");
      return 1;
    }
    auto* info = static_cast<AudioUnitCocoaViewInfo*>(malloc(size));
    AudioUnitGetProperty(unit, kAudioUnitProperty_CocoaUI, kAudioUnitScope_Global, 0, info, &size);
    NSURL* bundleURL = (__bridge NSURL*)info->mCocoaAUViewBundleLocation;
    NSString* className = (__bridge NSString*)info->mCocoaAUViewClass[0];
    Class factoryClass = [[NSBundle bundleWithURL:bundleURL] classNamed:className];
    id<AUCocoaUIBase> factory = [[factoryClass alloc] init];
    NSView* view = [factory uiViewForAudioUnit:unit withSize:NSZeroSize];
    if (!view) { std::printf("{\"error\":\"no view\"}\n"); return 1; }
    NSRect frame = NSMakeRect(80, 80, view.frame.size.width, view.frame.size.height);
    NSWindow* window = [[NSWindow alloc] initWithContentRect:frame styleMask:NSWindowStyleMaskTitled
                                                     backing:NSBackingStoreBuffered defer:NO];
    window.backgroundColor = NSColor.blackColor;  // What the user sees behind a hidden or empty view.
    window.contentView = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, frame.size.width, frame.size.height)];
    [window.contentView addSubview:view];
    [window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    spin(8.0);
    std::string tree;
    dump(window.contentView, 0, tree);
    // AppKit's own capture of the content view (what the views draw; macOS 15 removed CGWindowListCreateImage),
    // plus the system screenshot tool for the on-screen window as a second record.
    NSView* content = window.contentView;
    double lit = -1;
    NSBitmapImageRep* rep = [content bitmapImageRepForCachingDisplayInRect:content.bounds];
    if (rep) {
      [content cacheDisplayInRect:content.bounds toBitmapImageRep:rep];
      long total = 0, bright = 0;
      for (NSInteger y = 0; y < rep.pixelsHigh; y += 4)
        for (NSInteger x = 0; x < rep.pixelsWide; x += 4) {
          NSColor* c = [[rep colorAtX:x y:y] colorUsingColorSpace:NSColorSpace.sRGBColorSpace];
          ++total;
          if (c.redComponent + c.greenComponent + c.blueComponent > 0.06) ++bright;
        }
      lit = total ? double(bright) / total : 0;
      [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:@(argv[4]) atomically:YES];
    }
    std::string screen = std::string("/usr/sbin/screencapture -x -o -l ") + std::to_string((long)window.windowNumber) +
                         " '" + argv[4] + ".screen.png'";
    std::system(screen.c_str());
    std::printf("view class %s, size %.0fx%.0f, non-black share %.3f\n%s",
                NSStringFromClass([view class]).UTF8String, view.frame.size.width, view.frame.size.height, lit, tree.c_str());
    [view removeFromSuperview];  // iPlug2 closes the editor here, as Logic does when the window closes.
    spin(0.5);
    AudioUnitUninitialize(unit);
    AudioComponentInstanceDispose(unit);
    return 0;
  }
}
