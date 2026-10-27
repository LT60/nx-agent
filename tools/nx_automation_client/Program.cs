using System;
using System.IO;
using System.Reflection;
using System.Runtime.Loader;

namespace NxCli
{
    /// <summary>
    /// 最小验证程序：外部进程能不能连上 `-automation` 模式的 NX。
    ///
    /// 背景：安全策略挡死了 PowerShell 的 Add-Type / Assembly.LoadFrom
    /// （"运行时加载 .NET 代码"），所以只能走"正规编译"这条路。
    /// 如果 <c>NXOpen.Session.GetSession()</c> 在外部进程能连上，
    /// 那就不再需要 `-auto=` 那个半残窗口，也不用文件队列桥接。
    /// </summary>
    internal static class Program
    {
        private const string NxBin =
            @"D:\NX\NXBIN\managed_core";

        private static int Main(string[] args)
        {
            // NX 的托管程序集不在本程序目录里，运行时按需从 NX 目录加载。
            AssemblyLoadContext.Default.Resolving += (ctx, name) =>
            {
                string p = Path.Combine(NxBin, name.Name + ".dll");
                return File.Exists(p) ? ctx.LoadFromAssemblyPath(p) : null;
            };

            Console.WriteLine("=== NXOpen .NET external connect test ===");
            Console.WriteLine("assembly dir: " + NxBin);

            try
            {
                Assembly asm = Assembly.LoadFrom(Path.Combine(NxBin, "NXOpen.dll"));
                Console.WriteLine("loaded: " + asm.FullName);
            }
            catch (Exception ex)
            {
                Console.WriteLine("FAIL load NXOpen.dll: " + ex.GetType().Name + ": " + ex.Message);
                if (ex.InnerException != null)
                    Console.WriteLine("   inner: " + ex.InnerException.Message);
                return 2;
            }

            try
            {
                Type t = Type.GetType("NXOpen.Session, NXOpen");
                MethodInfo mi = t.GetMethod("GetSession",
                    BindingFlags.Public | BindingFlags.Static);
                object s = mi.Invoke(null, null);
                Console.WriteLine("OK: GetSession() -> " + s);

                PropertyInfo parts = s.GetType().GetProperty("Parts");
                object partsObj = parts.GetValue(s);
                PropertyInfo cnt = partsObj.GetType().GetProperty("Count");
                Console.WriteLine("    Parts.Count = " + cnt.GetValue(partsObj));

                PropertyInfo app = s.GetType().GetProperty("ApplicationName");
                Console.WriteLine("    ApplicationName = " + app.GetValue(s));
                return 0;
            }
            catch (Exception ex)
            {
                Exception e = (ex is TargetInvocationException && ex.InnerException != null)
                    ? ex.InnerException : ex;
                Console.WriteLine("FAIL GetSession: " + e.GetType().Name + ": " + e.Message);
                if (e.InnerException != null)
                    Console.WriteLine("   inner: " + e.InnerException.Message);
                return 1;
            }
        }
    }
}
