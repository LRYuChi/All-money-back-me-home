'use client';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-32 space-y-6">
      <div className="text-red-400 text-6xl">!</div>
      <h2 className="text-2xl font-bold text-white">發生錯誤</h2>
      <p className="text-gray-400 text-center max-w-md">
        {error.message || '頁面發生未預期的錯誤，請稍後再試。'}
      </p>
      <button
        onClick={reset}
        className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
      >
        重新載入
      </button>
    </div>
  );
}
