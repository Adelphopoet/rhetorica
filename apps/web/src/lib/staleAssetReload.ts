const STALE_CHUNK_MESSAGE =
  /Failed to fetch dynamically imported module|ChunkLoadError|Importing a module script failed|Loading chunk/;

export function isStaleChunkError(message: string | null | undefined): boolean {
  return STALE_CHUNK_MESSAGE.test(message ?? "");
}

export function extractAssetUrlFromMessage(message: string | null | undefined): string | null {
  if (!message) {
    return null;
  }
  const patterns = [
    /Failed to fetch dynamically imported module:\s*([^\s)]+)/i,
    /Loading chunk\s+\d+\s+failed[^\n]*?(https?:\/\/[^\s)']+|\/[^\s)']*\/_next\/static[^\s)']+)/i,
    /ChunkLoadError[^\n]*?(https?:\/\/[^\s)']+|\/[^\s)']*\/_next\/static[^\s)']+)/i,
    /(https?:\/\/[^\s)']+\/_next\/static\/(?:chunks|css)\/[^\s)']+)/i,
    /(\/[^\s)']*\/_next\/static\/(?:chunks|css)\/[^\s)']+)/i,
  ];
  for (const pattern of patterns) {
    const match = message.match(pattern);
    if (match) {
      const candidate = match[1] ?? match[0];
      if (candidate) {
        return candidate;
      }
    }
  }
  return null;
}

export function isStaleAssetUrl(url: string | null | undefined): boolean {
  return /\/_next\/static\/(?:chunks|css)\//.test(url ?? "");
}

// FR-RH-021.1 confirms staleness only for resources under /_next/static/(chunks|css)/,
// so a chunk-shaped message alone is not enough: the failing URL must be reliably known.
// A URL carried by the message is the failing import target and therefore authoritative.
// Without one, only the URL of the script that raised the error is trusted, which the
// rejection event does not provide.
export function isConfirmedStaleAssetFailure(
  message: string | null | undefined,
  scriptUrl: string | null | undefined
): boolean {
  if (!isStaleChunkError(message)) {
    return false;
  }
  const reportedUrl = extractAssetUrlFromMessage(message);
  if (reportedUrl) {
    return isStaleAssetUrl(reportedUrl);
  }
  return isStaleAssetUrl(scriptUrl);
}

export const STALE_ASSET_EVENT = "rhetorica:stale-asset-confirmed";
export const STALE_DETECTED_KEY = "__rh_stale_detected";

const STALE_EMIT_KEY = "__rh_stale_emitted";
const STALE_EMIT_COOLDOWN_MS = 2000;

// Runs in <head> before framework scripts load, independently of React hydration.
// Self-contained so it works even when the app bundle itself failed to hydrate.
// It only reports a confirmed stale JS/CSS asset error; recovery (reload or a
// manual notification) is decided by the React side.
export const STALE_ASSET_GUARD_INLINE_SCRIPT = `(function(){
function isChunkUrl(s){return /\\/_next\\/static\\/(?:chunks|css)\\//.test(s||"")}
function isChunkMsg(m){return /Failed to fetch dynamically imported module|ChunkLoadError|Importing a module script failed|Loading chunk/.test(m||"")}
function extractAssetUrlFromMessage(message){
  if(!message)return null;
  var patterns=[
    /Failed to fetch dynamically imported module:\\s*([^\\s)]+)/i,
    /Loading chunk\\s+\\d+\\s+failed[^\\n]*?(https?:\\/\\/[^\\s)']+|\\/[^\\s)']*\\/_next\\/static[^\\s)']+)/i,
    /ChunkLoadError[^\\n]*?(https?:\\/\\/[^\\s)']+|\\/[^\\s)']*\\/_next\\/static[^\\s)']+)/i,
    /(https?:\\/\\/[^\\s)']+\\/_next\\/static\\/(?:chunks|css)\\/[^\\s)']+)/i,
    /(\\/[^\\s)']*\\/_next\\/static\\/(?:chunks|css)\\/[^\\s)']+)/i,
  ];
  for(var i=0;i<patterns.length;i++){
    var m=String(message).match(patterns[i]);
    if(m){var c=m[1]||m[0];if(c)return c;}
  }
  return null;
}
function isConfirmedChunkFailure(message,scriptUrl){
  if(!isChunkMsg(message))return false;
  var reportedUrl=extractAssetUrlFromMessage(message);
  if(reportedUrl)return isChunkUrl(reportedUrl);
  return isChunkUrl(scriptUrl||"");
}
function confirmed(){
try{var now=Date.now(),key=${JSON.stringify(STALE_EMIT_KEY)};var prev=parseInt(sessionStorage.getItem(key)||"0",10);if(now-prev<${STALE_EMIT_COOLDOWN_MS})return;sessionStorage.setItem(key,String(now));sessionStorage.setItem(${JSON.stringify(STALE_DETECTED_KEY)},String(now))}catch(e){}
try{window.dispatchEvent(new CustomEvent(${JSON.stringify(STALE_ASSET_EVENT)}))}catch(e){}
}
window.addEventListener("error",function(e){
var t=e.target;
if(t&&t.tagName==="SCRIPT"&&isChunkUrl(t.src))return confirmed();
if(t&&t.tagName==="LINK"&&t.rel==="stylesheet"&&t.href&&isChunkUrl(t.href))return confirmed();
if(isConfirmedChunkFailure(e.message,e.filename)){confirmed();return}
},true);
window.addEventListener("unhandledrejection",function(e){var r=e.reason;var msg=r&&r.message?String(r.message):String(r);if(isConfirmedChunkFailure(msg,null)){confirmed();return}});
})();`;