import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-32 space-y-6">
      <div className="text-gray-600 text-8xl font-bold">404</div>
      <h2 className="text-2xl font-bold text-white">找不到頁面</h2>
      <p className="text-gray-400">
        您要找的頁面不存在或已被移除。
      </p>
      <Link
        href="/"
        className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
      >
        回到首頁
      </Link>
    </div>
  );
}
