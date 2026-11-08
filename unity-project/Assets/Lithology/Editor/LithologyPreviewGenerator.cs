using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace Subsurface.Lithology.Editor
{
    public static class LithologyPreviewGenerator
    {
        public const int DefaultResolution=128;
        static string Folder=>LithologyBuilder.Output+"/PreviewTextures";
        // Stateless local integer mixer. Does not touch UnityEngine.Random.
        static float H(int x,int y,int seed)
        { unchecked {uint h=(uint)(x*374761393+y*668265263+seed*144269);h=(h^(h>>13))*1274126177u;return (h^(h>>16))/(float)uint.MaxValue;} }
        static int Wrap(int a,int n)=>(a%n+n)%n;
        static float Noise(float x,float y,int cells,int seed)
        {
            x*=cells;y*=cells;int ix=Mathf.FloorToInt(x),iy=Mathf.FloorToInt(y);float fx=x-ix,fy=y-iy;fx=fx*fx*(3-2*fx);fy=fy*fy*(3-2*fy);
            return Mathf.Lerp(Mathf.Lerp(H(Wrap(ix,cells),Wrap(iy,cells),seed),H(Wrap(ix+1,cells),Wrap(iy,cells),seed),fx),Mathf.Lerp(H(Wrap(ix,cells),Wrap(iy+1,cells),seed),H(Wrap(ix+1,cells),Wrap(iy+1,cells),seed),fx),fy);
        }
        static Vector4 Cell(float u,float v,int seed,bool angular=false)
        {
            const int cells=7;float px=u*cells,py=v*cells;int ix=Mathf.FloorToInt(px),iy=Mathf.FloorToInt(py);float best=99,second=99,key=0;
            for(int y=-1;y<=1;y++)for(int x=-1;x<=1;x++)
            {
                int cx=ix+x,cy=iy+y;float rx=H(Wrap(cx,cells),Wrap(cy,cells),seed),ry=H(Wrap(cx,cells),Wrap(cy,cells),seed+3);
                float dx=px-cx-.2f-rx*.6f,dy=py-cy-.2f-ry*.6f;
                float dist=angular?Mathf.Max(Mathf.Abs(dx+dy*.35f),Mathf.Abs(dy-dx*.18f)):Mathf.Sqrt(dx*dx+dy*dy);
                if(dist<best){second=best;best=dist;key=H(Wrap(cx,cells),Wrap(cy,cells),seed+9);}else if(dist<second)second=dist;
            }
            return new Vector4(best,second-best,key,0);
        }
        static Color Palette(int b)
        {
            Color[] colors={new Color(.53f,.42f,.30f),new Color(.35f,.36f,.34f),new Color(.55f,.51f,.41f),new Color(.73f,.62f,.40f),new Color(.65f,.52f,.33f),new Color(.47f,.44f,.38f),new Color(.52f,.43f,.36f),new Color(.69f,.66f,.53f),new Color(.62f,.58f,.48f),new Color(.84f,.83f,.73f),new Color(.75f,.68f,.50f),new Color(.74f,.69f,.54f),new Color(.08f,.075f,.07f),new Color(.81f,.77f,.68f),new Color(.65f,.66f,.63f),new Color(.78f,.76f,.71f),new Color(.37f,.38f,.36f),new Color(.59f,.53f,.42f),new Color(.31f,.22f,.18f),new Color(.24f,.27f,.27f),new Color(.66f,.60f,.53f),new Color(.70f,.64f,.57f),new Color(.4f,.43f,.42f),new Color(.045f,.05f,.048f),new Color(.33f,.42f,.33f)};
            return colors[b-1];
        }
        static float Detail(float u,float v,int b,int seed,out Color mineral)
        {
            var cell=Cell(u,v,seed,b==7||b==9||b==22||b==23);float fine=Noise(u,v,32,seed+2);float h=.5f;mineral=Color.black;
            switch(b)
            {
                case 1:case 8:h=.46f+.05f*fine+.035f*Noise(u,v,8,seed);break;
                case 2:h=.45f+.08f*fine+.10f*Mathf.Sin(v*Mathf.PI*32);break;
                case 3:h=.45f+fine*.10f;break;
                case 4:case 5:h=Mathf.Clamp01((.49f-cell.x)*2);mineral=new Color(cell.z>.6f?.6f:0,cell.z<.22f?.7f:0,0);if(b==5)h=.30f+h*.55f;break;
                case 6:h=Mathf.SmoothStep(1,0,Mathf.InverseLerp(.12f,.48f,cell.x));mineral=new Color(h*.5f,cell.z*h*.4f,0);break;
                case 7:h=Mathf.SmoothStep(0,1,Mathf.InverseLerp(.025f,.2f,cell.y));mineral=new Color(h*.5f,cell.z*h*.4f,0);break;
                case 9:case 14:case 15:case 22:case 23:
                    h=.25f+Mathf.SmoothStep(0,1,Mathf.InverseLerp(0,.15f,cell.y))*.45f+cell.z*.12f;
                    mineral=new Color(cell.z>.45f?1:0,cell.z<.3f?1:0,cell.z);if(b==15)h=.45f+(h-.5f)*.3f;break;
                case 10:h=.55f-fine*.13f;break;
                case 11:
                    float x=Frac(u*6)-.5f,y=Frac(v*6)-.5f,rad=Mathf.Sqrt(x*x+y*y);
                    h=Mathf.Abs(rad-.31f)<.07f&&y>-.08f?.6f+.12f*Mathf.Cos(Mathf.Atan2(y,x)*18):.25f;mineral=new Color(h>.45f?1:0,0,0);break;
                case 12:h=Mathf.SmoothStep(1,0,Mathf.InverseLerp(.2f,.48f,cell.x));mineral=new Color(h*.6f,0,0);break;
                case 13:h=.45f+.08f*fine+.07f*Noise(u,v,4,seed);break;
                case 16:float bx=Mathf.Abs(Frac(u*7)-.5f),by=Mathf.Abs(Frac(v*7)-.5f);h=.3f+.4f*(1-Mathf.SmoothStep(.32f,.48f,Mathf.Max(bx,by)));break;
                case 17:case 24:h=.5f+.045f*Mathf.Sin(Noise(u,v,4,seed)*18)+fine*.02f;break;
                case 18:h=.35f+Mathf.SmoothStep(0,1,Mathf.InverseLerp(.03f,.16f,cell.y))*.2f+fine*.1f;mineral=new Color(cell.z>.65f?.7f:0,cell.z<.2f?.6f:0,0);break;
                case 19:h=.65f-.52f*(1-Mathf.SmoothStep(.16f,.34f,cell.x));mineral=new Color(0,h<.4f?1:0,0);break;
                case 20:case 21:h=.48f+fine*.08f;mineral=new Color(cell.z>.7f?.35f:0,cell.z<.3f?.5f:0,cell.z);break;
                default:h=.4f+.2f*Noise(u,v,4,seed);break;
            }
            return h;
        }
        static float Frac(float v)=>v-Mathf.Floor(v);
        [MenuItem("Tools/Lithology/Generate Preview Assets",priority=20)] public static void Generate()=>Generate(DefaultResolution);
        public static void Generate(int resolution)
        {
            if(LithologyBuilder.Catalog==null)throw new InvalidOperationException("Initialize first");
            int n=Mathf.Clamp(Mathf.ClosestPowerOfTwo(resolution),32,512);Directory.CreateDirectory(Folder);
            foreach(var set in LithologyBuilder.Catalog.bases)
            {
                int b=int.Parse(set.stableId.Substring(1)),seed=17001+b*997;int count=n*n;
                var macro=new Color[count];var detail=new Color[count];var height=new float[count];var regions=new Color[count];var surface=new Color[count];var heights=new Color[count];var normals=new Color[count];
                Color baseColor=Palette(b);
                for(int y=0;y<n;y++)for(int x=0;x<n;x++)
                {
                    float u=x/(float)(n-1),v=y/(float)(n-1);int i=y*n+x;
                    height[i]=Detail(u,v,b,seed,out regions[i]);float broad=Noise(u,v,4,seed);
                    macro[i]=baseColor*Mathf.Lerp(.88f,1.12f,broad);macro[i].a=1;
                    detail[i]=Color.white*Mathf.Lerp(.40f,.85f,height[i]);detail[i].a=1;
                    surface[i]=new Color(0,Mathf.Lerp(.94f,1,broad),0,b==24?.8f:b==10?.18f:.45f);
                    heights[i]=new Color(height[i],height[i],height[i],1);
                }
                for(int y=0;y<n;y++)for(int x=0;x<n;x++)
                {
                    int xx=x%(n-1),yy=y%(n-1);float dx=height[yy*n+Wrap(xx+1,n-1)]-height[yy*n+Wrap(xx-1,n-1)];float dy=height[Wrap(yy+1,n-1)*n+xx]-height[Wrap(yy-1,n-1)*n+xx];
                    // Y+ tangent-space normal from negative height gradient: raised features shade outward.
                    Vector3 normal=new Vector3(-dx*2,-dy*2,1).normalized;normals[y*n+x]=new Color(normal.x*.5f+.5f,normal.y*.5f+.5f,normal.z*.5f+.5f,1);
                }
                var albedo=Save(set.stableId+"-Macro",macro,n,true);var dc=Save(set.stableId+"-Grain",detail,n,true);var dn=Save(set.stableId+"-Normal",normals,n,false,true);
                var mask=Save(set.stableId+"-Regions",regions,n,false);var packed=Save(set.stableId+"-Surface",surface,n,false);var hmap=Save(set.stableId+"-Height",heights,n,false);
                Undo.RecordObject(set,"Generate owned lithology previews");
                Assign(ref set.albedo,albedo);Assign(ref set.detailAlbedo,dc);Assign(ref set.detailNormal,dn);Assign(ref set.detailMask,mask);Assign(ref set.surface,packed);Assign(ref set.height,hmap);
                // Never claim or overwrite a real set on regeneration. A user can also retain preview
                // macros and assign only a real detail channel; only owned paths are regenerated.
                if(set.illustrativePreview){set.independentGrainScale=true;set.macroTileMetres=1;set.detailTileMetres=.01f;}
                set.revision++;EditorUtility.SetDirty(set);
            }
            GenerateDetails(n);AssetDatabase.SaveAssets();Debug.Log("Lithology: 25 deterministic illustrative preview sets plus D01-D11 generated at "+n+"px.");
        }
        static void Assign(ref Texture2D target,Texture2D generated) {if(target==null||AssetDatabase.GetAssetPath(target).StartsWith(Folder+"/",StringComparison.Ordinal))target=generated;}
        public static Texture2D Save(string name,Color[] colors,int n,bool srgb,bool normal=false)
        {
            var t=new Texture2D(n,n,TextureFormat.RGBA32,false,!srgb);t.SetPixels(colors);t.Apply();string path=Folder+"/"+name+".png";File.WriteAllBytes(path,t.EncodeToPNG());UnityEngine.Object.DestroyImmediate(t);
            AssetDatabase.ImportAsset(path,ImportAssetOptions.ForceSynchronousImport);
            var importer=(TextureImporter)AssetImporter.GetAtPath(path);importer.textureType=normal?TextureImporterType.NormalMap:TextureImporterType.Default;importer.sRGBTexture=srgb&&!normal;importer.wrapMode=TextureWrapMode.Repeat;importer.filterMode=FilterMode.Trilinear;importer.mipmapEnabled=true;importer.textureCompression=TextureImporterCompression.Uncompressed;importer.isReadable=true;importer.SaveAndReimport();
            return AssetDatabase.LoadAssetAtPath<Texture2D>(path);
        }
        static void GenerateDetails(int n)
        {
            string[] names={"D01-Coverage","D02-Lamination","D03-Grains","D04-Rounded","D05-Angular","D06-Shell","D06-Ooid","D07-Crystal","D07-Glass","D07-Flake","D08-Vein","D09-PoreFracture","D10-Welded","D11-ConditionalDiagnostic"};
            foreach(string name in names)
            {
                var data=new Color[n*n];int seed=31991;
                for(int y=0;y<n;y++)for(int x=0;x<n;x++)
                {
                    float u=x/(float)(n-1),v=y/(float)(n-1);var c=Cell(u,v,seed,name.Contains("Angular"));float f;
                    if(name.Contains("Lamination"))f=.5f+.5f*Mathf.Sin(v*24*Mathf.PI);
                    else if(name.Contains("Shell")){float a=Frac(u*6)-.5f,b=Frac(v*6)-.5f;f=Mathf.Abs(Mathf.Sqrt(a*a+b*b)-.3f)<.06f&&b>0?1:0;}
                    else if(name.Contains("Ooid"))f=(1-Mathf.SmoothStep(.25f,.45f,c.x))*(.6f+.4f*Mathf.Cos(c.x*80));
                    else if(name.Contains("Angular")||name.Contains("Crystal"))f=Mathf.SmoothStep(0,.15f,c.y);
                    else if(name.Contains("Rounded")||name.Contains("Grains")||name.Contains("Pore"))f=1-Mathf.SmoothStep(.16f,.42f,c.x);
                    else if(name.Contains("Glass"))f=Mathf.Clamp01(1-(Mathf.Abs(Frac(u*8)-.5f)+Mathf.Abs(Frac(v*8)-.5f)*2)*3);
                    else if(name.Contains("Flake"))f=Mathf.Abs(Frac(v*10)-.5f)<.08f&&Mathf.Abs(Frac(u*8)-.5f)<.35f?1:0;
                    else if(name.Contains("Vein"))f=1-Mathf.SmoothStep(.02f,.09f,Mathf.Abs(Mathf.Sin((u+Noise(u,v,4,seed)*.1f)*8*Mathf.PI)));
                    else if(name.Contains("Welded"))f=1-Mathf.SmoothStep(.15f,.45f,Mathf.Sqrt(Mathf.Pow(Frac(u*4)-.5f,2)+Mathf.Pow((Frac(v*16)-.5f)*3,2)));
                    else if(name.Contains("Conditional"))f=(x%16<8)^(y%16<8)?1:0;
                    else f=Noise(u,v,8,seed);
                    data[y*n+x]=new Color(f,f,f,1);
                }
                // Exact boundary agreement, including the diagnostic placeholder.
                for(int y=0;y<n;y++)data[y*n+n-1]=data[y*n];for(int x=0;x<n;x++)data[(n-1)*n+x]=data[x];Save(name,data,n,false);
            }
        }
    }
}
