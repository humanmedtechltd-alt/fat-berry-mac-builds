// Proves on a real Mac how a WebView editor page loads, without a host or a licence.
//
//   clang++ -std=c++20 -fobjc-arc -framework Cocoa -framework WebKit page_probe.mm -o page_probe
//   page_probe memory|file /path/to/Resources/web/index.html snapshot.png
//
// "memory" loads the page the way the 13 November 2026 fix does (iPlug2's
// LoadHTML: loadHTMLString with no base URL). "file" loads it the old way
// (iPlug2's LoadFile: loadFileURL). Both go through the same navigation filter
// as FatBerryFB985/FatBerryWorkHorse::OnCanNavigateToURL before the fix
// (virtual host, about:, data:text/html). The probe records each navigation
// decision, whether the page sent its "ready" and whether WebKit still
// reported loading at that moment (iPlug2's Mac EvaluateJavaScript does
// nothing then), whether a script sent straight to the WKWebView at that
// moment ran, and how much of a snapshot of the window is not black.
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#include <cstdio>
#include <string>

static bool allowedBeforeFix(NSString* url) {
  return [url hasPrefix:@"https://iplug.example/"] || [url hasPrefix:@"about:"] || [url hasPrefix:@"data:text/html"];
}

@interface Probe : NSObject <WKNavigationDelegate, WKScriptMessageHandler>
@property(nonatomic) NSMutableArray<NSString*>* decisions;
@property(nonatomic) BOOL finished, failed, sawReady, loadingAtReady;
@property(nonatomic, weak) WKWebView* view;
@end

@implementation Probe
- (void)webView:(WKWebView*)webView decidePolicyForNavigationAction:(WKNavigationAction*)action
    decisionHandler:(void (^)(WKNavigationActionPolicy))handler {
  NSString* url = action.request.URL.absoluteString ?: @"";
  const BOOL allow = allowedBeforeFix(url);
  [self.decisions addObject:[NSString stringWithFormat:@"%@ %@", allow ? @"allow" : @"cancel", url]];
  handler(allow ? WKNavigationActionPolicyAllow : WKNavigationActionPolicyCancel);
}
- (void)webView:(WKWebView*)webView didFinishNavigation:(WKNavigation*)navigation { self.finished = YES; }
- (void)webView:(WKWebView*)webView didFailNavigation:(WKNavigation*)n withError:(NSError*)e { self.failed = YES; }
- (void)webView:(WKWebView*)webView didFailProvisionalNavigation:(WKNavigation*)n withError:(NSError*)e { self.failed = YES; }
- (void)userContentController:(WKUserContentController*)controller didReceiveScriptMessage:(WKScriptMessage*)message {
  NSString* body = [NSString stringWithFormat:@"%@", message.body];
  if (!self.sawReady && [body containsString:@"ready"]) {
    self.sawReady = YES;
    self.loadingAtReady = self.view.isLoading;
    // What runPageScript does: straight to the WKWebView, loading or not.
    [self.view evaluateJavaScript:@"window.__probeHello = 1;" completionHandler:nil];
  }
}
@end

static void spin(double seconds) {
  [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:seconds]];
}

int main(int argc, char** argv) {
  @autoreleasepool {
    if (argc != 4) { std::fprintf(stderr, "usage: page_probe memory|file index.html snapshot.png\n"); return 2; }
    const std::string mode = argv[1];
    NSString* path = [NSString stringWithUTF8String:argv[2]];
    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];

    Probe* probe = [Probe new];
    probe.decisions = [NSMutableArray array];
    WKWebViewConfiguration* config = [WKWebViewConfiguration new];
    WKUserContentController* controller = [WKUserContentController new];
    [controller addScriptMessageHandler:probe name:@"callback"];
    // The same helper iPlug2 injects for the page's messages.
    [controller addUserScript:[[WKUserScript alloc]
        initWithSource:@"function IPlugSendMsg(m) { webkit.messageHandlers.callback.postMessage(m); }"
        injectionTime:WKUserScriptInjectionTimeAtDocumentStart forMainFrameOnly:YES]];
    config.userContentController = controller;

    NSRect frame = NSMakeRect(0, 0, 1100, 700);
    NSWindow* window = [[NSWindow alloc] initWithContentRect:frame styleMask:NSWindowStyleMaskTitled
                                                     backing:NSBackingStoreBuffered defer:NO];
    WKWebView* web = [[WKWebView alloc] initWithFrame:frame configuration:config];
    web.navigationDelegate = probe;
    probe.view = web;
    window.contentView = web;
    [window makeKeyAndOrderFront:nil];

    if (mode == "memory") {
      NSString* html = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
      if (!html) { std::fprintf(stderr, "cannot read %s\n", argv[2]); return 2; }
      [web loadHTMLString:html baseURL:nil];
    } else {
      NSURL* page = [NSURL fileURLWithPath:path];
      [web loadFileURL:page allowingReadAccessToURL:[page URLByDeletingLastPathComponent]];
    }
    for (int i = 0; i < 100 && !(probe.finished && probe.sawReady); ++i) spin(0.1);
    spin(1.5);

    __block NSString* state = @"(no answer)";
    __block BOOL answered = NO;
    [web evaluateJavaScript:@"JSON.stringify({hello: window.__probeHello === 1, elements: document.querySelectorAll('*').length, text: (document.body ? document.body.innerText.length : -1), title: document.title})"
          completionHandler:^(id result, NSError* error) { state = error ? error.localizedDescription : result; answered = YES; }];
    for (int i = 0; i < 50 && !answered; ++i) spin(0.1);

    __block double lit = -1;
    __block BOOL shot = NO;
    [web takeSnapshotWithConfiguration:nil completionHandler:^(NSImage* image, NSError* error) {
      if (image) {
        NSBitmapImageRep* rep = [[NSBitmapImageRep alloc] initWithData:image.TIFFRepresentation];
        long total = 0, nonBlack = 0;
        for (NSInteger y = 0; y < rep.pixelsHigh; y += 4)
          for (NSInteger x = 0; x < rep.pixelsWide; x += 4) {
            NSColor* c = [[rep colorAtX:x y:y] colorUsingColorSpace:NSColorSpace.sRGBColorSpace];
            ++total;
            if (c.redComponent + c.greenComponent + c.blueComponent > 0.06) ++nonBlack;
          }
        lit = total ? double(nonBlack) / total : 0;
        [[rep representationUsingType:NSBitmapImageFileTypePNG properties:@{}]
            writeToFile:[NSString stringWithUTF8String:argv[3]] atomically:YES];
      }
      shot = YES;
    }];
    for (int i = 0; i < 50 && !shot; ++i) spin(0.1);

    std::printf("{\"mode\":\"%s\",\"decisions\":\"%s\",\"finished\":%s,\"failed\":%s,\"ready\":%s,"
                "\"loadingWhenReadyArrived\":%s,\"page\":%s,\"nonBlackShare\":%.3f}\n",
                mode.c_str(), [[probe.decisions componentsJoinedByString:@" | "] UTF8String],
                probe.finished ? "true" : "false", probe.failed ? "true" : "false",
                probe.sawReady ? "true" : "false", probe.loadingAtReady ? "true" : "false",
                answered && [state hasPrefix:@"{"] ? state.UTF8String : "null", lit);
    return 0;
  }
}
