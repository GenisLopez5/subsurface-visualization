using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace Subsurface.Explorer
{
    public static class ExplorerDialogs
    {
        public static Func<string[]> PickOverride;
        public static Func<string,string> SaveOverride;
        public static string[] Pick()=>PickOverride!=null?PickOverride():Dialog(false,null);
        public static string Save(string name)=>SaveOverride!=null?SaveOverride(name):First(Dialog(true,name));
        static string First(string[] values)=>values.Length==0?null:values[0];
        static string[] Dialog(bool save,string name)
        {
#if UNITY_EDITOR_WIN || UNITY_STANDALONE_WIN
            const int capacity=65536;var memory=Marshal.AllocHGlobal(capacity*2);
            try
            {
                for(int i=0;i<capacity;i++)Marshal.WriteInt16(memory,i*2,0);
                if(save&&name!=null)Marshal.Copy((name+'\0').ToCharArray(),0,memory,name.Length+1);
                var data=new OpenFileName{size=Marshal.SizeOf<OpenFileName>(),owner=GetActiveWindow(),filter=save?"ZIP archive\0*.zip\0\0":"TIFF documents\0*.tif;*.tiff\0\0",file=memory,maxFile=capacity,title=save?"Save documents":"Upload TIFF documents",defaultExtension=save?"zip":null,flags=0x00080000|0x00000800|0x00000008|(save?0x2:0x1000|0x200)};
                bool accepted=save?GetSaveFileName(ref data):GetOpenFileName(ref data);
                if(!accepted){int error=CommDlgExtendedError();if(error!=0)throw new InvalidOperationException("File dialog error "+error);return Array.Empty<string>();}
                var parts=new List<string>();int offset=0;while(offset<capacity){string part=Marshal.PtrToStringUni(IntPtr.Add(memory,offset*2));if(string.IsNullOrEmpty(part))break;parts.Add(part);offset+=part.Length+1;}
                if(parts.Count<=1)return parts.ToArray();var paths=new string[parts.Count-1];for(int i=1;i<parts.Count;i++)paths[i-1]=System.IO.Path.Combine(parts[0],parts[i]);return paths;
            }
            finally{Marshal.FreeHGlobal(memory);}
#else
            throw new PlatformNotSupportedException("Native file dialogs currently require Windows.");
#endif
        }
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct OpenFileName
        {
            public int size;public IntPtr owner,instance;[MarshalAs(UnmanagedType.LPWStr)]public string filter;public IntPtr customFilter;public int maxCustomFilter,filterIndex;public IntPtr file;public int maxFile;public IntPtr fileTitle;public int maxFileTitle;[MarshalAs(UnmanagedType.LPWStr)]public string initialDirectory,title;public int flags;public short fileOffset,fileExtension;[MarshalAs(UnmanagedType.LPWStr)]public string defaultExtension;public IntPtr customData,hook;[MarshalAs(UnmanagedType.LPWStr)]public string templateName;public IntPtr reserved;public int reserved2,flagsEx;
        }
        [DllImport("comdlg32.dll",CharSet=CharSet.Unicode)]static extern bool GetOpenFileName(ref OpenFileName data);
        [DllImport("comdlg32.dll",CharSet=CharSet.Unicode)]static extern bool GetSaveFileName(ref OpenFileName data);
        [DllImport("comdlg32.dll")]static extern int CommDlgExtendedError();
        [DllImport("user32.dll")]static extern IntPtr GetActiveWindow();
    }
}
