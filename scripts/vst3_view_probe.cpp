// Opens a VST3 plug-in's editor in a window, as a Windows host does, and reports what is on screen.
//
//   vst3_view_probe "C:\Program Files\Common Files\VST3\FatBerryFB985.vst3" window.bmp
//
// Loads the module, creates the audio component (iPlug2 plug-ins are single-component effects, so the edit
// controller is the same object), asks for the editor view and attaches it to a top-level window. After ten
// seconds of message pumping it prints every child window (class, size, visible) and captures the window with
// PrintWindow (full content), reporting how much of it is not black.
//
// Build in an x64 developer prompt, with the VST3 SDK from the plug-in's vendor/vst3sdk as %SDK%:
//   cl /nologo /std:c++20 /EHsc /O2 /I%SDK% vst3_view_probe.cpp %SDK%\pluginterfaces\base\funknown.cpp
//      %SDK%\pluginterfaces\base\coreiids.cpp user32.lib gdi32.lib ole32.lib
// To see what a customer who has not activated sees, move the product's .fblicence and .state.json out of
// %LOCALAPPDATA%\Fat Berry DSP\Licensing\v1 for the run, then put them back unchanged.
#include <windows.h>
#include <cstdio>
#include <string>
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/gui/iplugview.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivsthostapplication.h"

namespace Steinberg { namespace Vst {
DEF_CLASS_IID(IComponent)
DEF_CLASS_IID(IEditController)
DEF_CLASS_IID(IHostApplication)
} }

using namespace Steinberg;
using namespace Steinberg::Vst;

class Host final : public IHostApplication {
public:
  tresult PLUGIN_API getName(String128 name) override {
    const char16_t text[] = u"Fat Berry probe";
    for (size_t i = 0; i < sizeof(text) / sizeof(text[0]); ++i) name[i] = static_cast<char16>(text[i]);
    return kResultOk;
  }
  tresult PLUGIN_API createInstance(TUID, TUID, void** obj) override { *obj = nullptr; return kResultFalse; }
  tresult PLUGIN_API queryInterface(const TUID iid, void** obj) override {
    if (FUnknownPrivate::iidEqual(iid, IHostApplication::iid) || FUnknownPrivate::iidEqual(iid, FUnknown::iid)) {
      *obj = this;
      return kResultOk;
    }
    *obj = nullptr;
    return kNoInterface;
  }
  uint32 PLUGIN_API addRef() override { return 1; }
  uint32 PLUGIN_API release() override { return 1; }
};

static BOOL CALLBACK listChild(HWND child, LPARAM depthBase) {
  wchar_t name[256] = {};
  GetClassNameW(child, name, 255);
  RECT r{};
  GetWindowRect(child, &r);
  HWND parent = GetParent(child);
  int depth = 0;
  for (HWND p = parent; p && p != reinterpret_cast<HWND>(depthBase); p = GetParent(p)) ++depth;
  std::printf("%*s%ls %ldx%ld visible=%s\n", 2 + depth * 2, "", name, r.right - r.left, r.bottom - r.top,
              IsWindowVisible(child) ? "yes" : "no");
  return TRUE;
}

static void pump(DWORD milliseconds) {
  const DWORD end = GetTickCount() + milliseconds;
  MSG msg;
  while (GetTickCount() < end) {
    while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
      TranslateMessage(&msg);
      DispatchMessageW(&msg);
    }
    Sleep(10);
  }
}

int wmain(int argc, wchar_t** argv) {
  if (argc != 3) { std::fprintf(stderr, "usage: vst3_view_probe BUNDLE.vst3 out.bmp\n"); return 2; }
  SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
  CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  std::wstring bundle = argv[1];
  std::wstring name = bundle.substr(bundle.find_last_of(L"\\/") + 1);
  HMODULE module = LoadLibraryW((bundle + L"\\Contents\\x86_64-win\\" + name).c_str());
  if (!module) { std::printf("cannot load module (error %lu)\n", GetLastError()); return 1; }
  using InitFn = bool (*)();
  if (auto init = reinterpret_cast<InitFn>(GetProcAddress(module, "InitDll"))) init();
  auto getFactory = reinterpret_cast<GetFactoryProc>(GetProcAddress(module, "GetPluginFactory"));
  IPluginFactory* factory = getFactory ? getFactory() : nullptr;
  if (!factory) { std::printf("no factory\n"); return 1; }
  Host host;
  IComponent* component = nullptr;
  for (int32 i = 0; i < factory->countClasses() && !component; ++i) {
    PClassInfo info;
    factory->getClassInfo(i, &info);
    if (std::string(info.category) == kVstAudioEffectClass)
      factory->createInstance(info.cid, IComponent::iid, reinterpret_cast<void**>(&component));
  }
  if (!component) { std::printf("no audio component\n"); return 1; }
  component->initialize(&host);
  IEditController* controller = nullptr;
  if (component->queryInterface(IEditController::iid, reinterpret_cast<void**>(&controller)) != kResultOk) {
    TUID cid;
    if (component->getControllerClassId(cid) == kResultOk)
      factory->createInstance(cid, IEditController::iid, reinterpret_cast<void**>(&controller));
    if (controller) controller->initialize(&host);
  }
  if (!controller) { std::printf("no edit controller\n"); return 1; }
  IPlugView* view = controller->createView(ViewType::kEditor);
  if (!view || view->isPlatformTypeSupported(kPlatformTypeHWND) != kResultTrue) { std::printf("no HWND editor\n"); return 1; }
  ViewRect size{};
  view->getSize(&size);
  WNDCLASSW wc{};
  wc.lpfnWndProc = DefWindowProcW;
  wc.hInstance = GetModuleHandleW(nullptr);
  wc.lpszClassName = L"FatBerryProbeHost";
  wc.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));  // What a user sees behind a hidden view.
  RegisterClassW(&wc);
  RECT frame{0, 0, size.getWidth(), size.getHeight()};
  AdjustWindowRect(&frame, WS_OVERLAPPEDWINDOW, FALSE);
  HWND window = CreateWindowW(wc.lpszClassName, L"probe", WS_OVERLAPPEDWINDOW | WS_VISIBLE, 60, 60,
                              frame.right - frame.left, frame.bottom - frame.top, nullptr, nullptr, wc.hInstance, nullptr);
  view->attached(window, kPlatformTypeHWND);
  pump(10000);
  std::printf("editor %dx%d, child windows:\n", size.getWidth(), size.getHeight());
  EnumChildWindows(window, listChild, reinterpret_cast<LPARAM>(window));
  RECT client{};
  GetClientRect(window, &client);
  const int w = client.right, h = client.bottom;
  HDC screen = GetDC(window);
  HDC memory = CreateCompatibleDC(screen);
  BITMAPINFO bi{};
  bi.bmiHeader.biSize = sizeof(bi.bmiHeader);
  bi.bmiHeader.biWidth = w;
  bi.bmiHeader.biHeight = -h;
  bi.bmiHeader.biPlanes = 1;
  bi.bmiHeader.biBitCount = 32;
  void* bits = nullptr;
  HBITMAP bitmap = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
  SelectObject(memory, bitmap);
  PrintWindow(window, memory, PW_CLIENTONLY | PW_RENDERFULLCONTENT);
  long total = 0, bright = 0;
  auto* pixels = static_cast<unsigned char*>(bits);
  for (int y = 0; y < h; y += 4)
    for (int x = 0; x < w; x += 4) {
      const unsigned char* p = pixels + (static_cast<size_t>(y) * w + x) * 4;
      ++total;
      if (p[0] + p[1] + p[2] > 15) ++bright;
    }
  std::printf("non-black share %.3f\n", total ? double(bright) / total : 0.0);
  BITMAPFILEHEADER fh{};
  fh.bfType = 0x4D42;
  fh.bfOffBits = sizeof(fh) + sizeof(bi.bmiHeader);
  fh.bfSize = fh.bfOffBits + static_cast<DWORD>(w) * h * 4;
  if (FILE* out = _wfopen(argv[2], L"wb")) {
    fwrite(&fh, sizeof(fh), 1, out);
    fwrite(&bi.bmiHeader, sizeof(bi.bmiHeader), 1, out);
    fwrite(bits, 1, static_cast<size_t>(w) * h * 4, out);
    fclose(out);
  }
  view->removed();
  view->release();
  pump(500);
  DestroyWindow(window);
  return 0;
}
