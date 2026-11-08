using System;
using System.IO;
using System.Runtime.InteropServices;
using UnityEngine;

namespace Subsurface.Borehole
{
    public sealed class BoreholeDocumentImage
    {
        public Texture2D Texture;
        public int OriginalWidth,OriginalHeight;
        public static BoreholeDocumentImage Load(string path)
        {
            if(string.IsNullOrWhiteSpace(path)||!File.Exists(path))throw new IOException("Choose an existing document image.");
            if(new FileInfo(path).Length>512L*1024*1024)throw new IOException("The debug image loader supports files up to 512 MB.");
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
            return LoadWindows(Path.GetFullPath(path));
#else
            string extension=Path.GetExtension(path).ToLowerInvariant();
            if(extension==".tif"||extension==".tiff")throw new PlatformNotSupportedException("TIFF file decoding is available on Windows. Supply a decoded texture through SetAnalysisResult on this platform.");
            var texture=new Texture2D(2,2,TextureFormat.RGBA32,false);
            if(!texture.LoadImage(File.ReadAllBytes(path))){UnityEngine.Object.Destroy(texture);throw new IOException("The image could not be decoded.");}
            return new BoreholeDocumentImage{Texture=texture,OriginalWidth=texture.width,OriginalHeight=texture.height};
#endif
        }
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
        // Windows Imaging Component handles TIFF compression without a runtime package dependency.
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int CreateDecoder(IntPtr self,[MarshalAs(UnmanagedType.LPWStr)]string file,IntPtr vendor,uint access,uint metadata,out IntPtr decoder);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int CreateObject(IntPtr self,out IntPtr result);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int GetUInt(IntPtr self,out uint value);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int GetFrame(IntPtr self,uint index,out IntPtr frame);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int GetSize(IntPtr self,out uint width,out uint height);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int Scale(IntPtr self,IntPtr source,uint width,uint height,uint interpolation);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int ConvertPixels(IntPtr self,IntPtr source,ref Guid format,uint dither,IntPtr palette,double alpha,uint paletteType);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]delegate int CopyPixels(IntPtr self,IntPtr rect,uint stride,uint size,[Out]byte[] pixels);
        [DllImport("ole32.dll")]static extern int CoCreateInstance(ref Guid clsid,IntPtr outer,uint context,ref Guid iid,out IntPtr result);
        [DllImport("ole32.dll")]static extern int CoInitializeEx(IntPtr reserved,uint flags);
        [DllImport("ole32.dll")]static extern void CoUninitialize();
        static T Method<T>(IntPtr instance,int slot) where T:Delegate=>Marshal.GetDelegateForFunctionPointer<T>(Marshal.ReadIntPtr(Marshal.ReadIntPtr(instance),slot*IntPtr.Size));
        static void Check(int result){if(result<0)Marshal.ThrowExceptionForHR(result);}
        static BoreholeDocumentImage LoadWindows(string path)
        {
            IntPtr factory=IntPtr.Zero,decoder=IntPtr.Zero,frame=IntPtr.Zero,scaler=IntPtr.Zero,converter=IntPtr.Zero;Texture2D texture=null;bool uninitialize=false;
            try
            {
                int initialized=CoInitializeEx(IntPtr.Zero,2);if(initialized>=0)uninitialize=true;else if(initialized!=unchecked((int)0x80010106))Check(initialized);
                var clsid=new Guid("cacaf262-9370-4615-a13b-9f5539da4c0a");var iid=new Guid("ec5ec8a9-c395-4314-9c77-54d7a935ff70");
                Check(CoCreateInstance(ref clsid,IntPtr.Zero,1,ref iid,out factory));
                Check(Method<CreateDecoder>(factory,3)(factory,path,IntPtr.Zero,0x80000000,0,out decoder));
                Check(Method<GetUInt>(decoder,12)(decoder,out uint pages));if(pages!=1)throw new IOException("Use a single-page image. This TIFF contains "+pages+" pages.");
                Check(Method<GetFrame>(decoder,13)(decoder,0,out frame));Check(Method<GetSize>(frame,3)(frame,out uint width,out uint height));
                if(width==0||height==0||width>1000000||height>1000000)throw new IOException("Invalid or unsupported document dimensions.");
                int limit=Math.Min(SystemInfo.maxTextureSize,16384);double factor=Math.Min(1,Math.Min(limit/(double)Math.Max(width,height),Math.Sqrt(32_000_000d/(width*(double)height))));
                uint w=(uint)Math.Max(1,Math.Round(width*factor)),h=(uint)Math.Max(1,Math.Round(height*factor));IntPtr source=frame;
                if(w!=width||h!=height){Check(Method<CreateObject>(factory,11)(factory,out scaler));Check(Method<Scale>(scaler,8)(scaler,frame,w,h,3));source=scaler;}
                Check(Method<CreateObject>(factory,10)(factory,out converter));var bgra=new Guid("6fddc324-4e03-4bfe-b185-3d77768dc90f");
                Check(Method<ConvertPixels>(converter,8)(converter,source,ref bgra,0,IntPtr.Zero,0,0));
                int stride=checked((int)w*4);var bytes=new byte[checked(stride*(int)h)];Check(Method<CopyPixels>(converter,7)(converter,IntPtr.Zero,(uint)stride,(uint)bytes.Length,bytes));
                var row=new byte[stride];for(int y=0;y<h/2;y++){int a=y*stride,b=((int)h-1-y)*stride;Buffer.BlockCopy(bytes,a,row,0,stride);Buffer.BlockCopy(bytes,b,bytes,a,stride);Buffer.BlockCopy(row,0,bytes,b,stride);}
                texture=new Texture2D((int)w,(int)h,TextureFormat.BGRA32,false){name="Borehole document image",hideFlags=HideFlags.DontSave,wrapMode=TextureWrapMode.Clamp};
                texture.LoadRawTextureData(bytes);texture.Apply(false,true);
                return new BoreholeDocumentImage{Texture=texture,OriginalWidth=(int)width,OriginalHeight=(int)height};
            }
            catch{if(texture!=null)UnityEngine.Object.Destroy(texture);throw;}
            finally{if(converter!=IntPtr.Zero)Marshal.Release(converter);if(scaler!=IntPtr.Zero)Marshal.Release(scaler);if(frame!=IntPtr.Zero)Marshal.Release(frame);if(decoder!=IntPtr.Zero)Marshal.Release(decoder);if(factory!=IntPtr.Zero)Marshal.Release(factory);if(uninitialize)CoUninitialize();}
        }
#endif
    }
}
